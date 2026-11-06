"""Evaluación propia del agente (S3): corre casos contra Grok y los califica.

Cada caso (eval/casos.jsonl) trae una entrada del tendero (texto, audio o foto) y lo que se espera.
El agente corre de verdad contra la pasarela, pero sobre una tienda fija (eval/tienda.json): las
herramientas no tocan la base de datos, solo anotan qué borradores habría creado el bot.

Qué se puede esperar de un caso (todo opcional):
- herramientas: deben llamarse todas.            ej. ["registrar_ventas"]
- herramientas_alguna: debe llamarse al menos una. ej. ["armar_pedido", "ajustar_pedido"]
- no_debe_llamar: no debe llamarse ninguna.
- ventas: borrador de ventas exacto [{sku, cantidad}]. [] = no debe quedar ninguna venta.
- cambios: cambios al pedido exactos [{sku, accion, cantidad, unidad?}].
- conteo: conteo de la foto [{sku, cantidad, tolerancia?}].
- respuesta_contiene: textos que deben estar en la respuesta ("a|b" = cualquiera de los dos).
- respuesta_no_contiene: textos que no deben estar.
"""

import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from app.ai.agent import run_agent
from app.ai.grok_client import extract_stock, transcribe_audio
from app.ai.matching import match_stock_lines
from app.ai.prompts import AGENT_SYSTEM_PROMPT
from app.ai.tools import StoreActions, build_tools
from app.ai.usage import TaskUsage, track_task
from app.schemas import OrderChange, SaleLine

ACTION_TOOLS = ("registrar_ventas", "armar_pedido", "ajustar_pedido")
MEDIA_TYPES = {".ogg": "audio/ogg", ".oga": "audio/ogg", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
               ".wav": "audio/wav", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


@dataclass
class Observed:
    """Lo que hizo el agente en un caso."""

    tools: list[str] = field(default_factory=list)
    sales: list[tuple[str, int]] = field(default_factory=list)
    changes: list[tuple[str, str, int, str]] = field(default_factory=list)
    count: list[tuple[str, int]] = field(default_factory=list)
    reply: str = ""
    transcript: str | None = None
    finished: bool = True


def fixed_store_actions(store: dict, observed: Observed, order_open: bool) -> StoreActions:
    """Herramientas sobre la tienda fija. Igual que el bot: el último borrador de cada tipo es el que vale."""

    async def stock() -> list[dict]:
        return store["stock"]

    async def alerts() -> dict:
        return store["alertas"]

    async def draft_sales(lines: list[SaleLine]) -> dict:
        if not lines:
            return {"borrador": "no_creado", "motivo": "Ningún producto se reconoció"}
        observed.sales = [(str(line.sku), line.quantity) for line in lines]
        return {"borrador": "creado"}

    async def suggest_order() -> dict:
        return store["pedido_sugerido"]

    async def change_order(changes: list[OrderChange]) -> dict:
        if not order_open:
            return {"error": "No hay un pedido abierto. Arma uno primero con armar_pedido."}
        observed.changes = [(str(c.sku), c.action, c.quantity, c.unit) for c in changes]
        return {"borrador": "actualizado", "nota": "El tendero lo envía con el botón del pedido."}

    return StoreActions(
        catalog=store["catalogo"],
        stock=stock,
        alerts=alerts,
        draft_sales=draft_sales,
        suggest_order=suggest_order,
        change_order=change_order,
    )


def _fold(text: str) -> str:
    """Minúsculas y sin tildes, para comparar respuestas."""
    normalized = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _sum_by_sku(rows) -> Counter:
    total: Counter = Counter()
    for sku, quantity in rows:
        total[sku] += quantity
    return total


def evaluate(expected: dict, observed: Observed) -> list[str]:
    """Las fallas del caso (vacío = pasó)."""
    failures: list[str] = []
    called = set(observed.tools)

    missing = [name for name in expected.get("herramientas", []) if name not in called]
    if missing:
        failures.append(f"no llamó {', '.join(missing)} (llamó: {', '.join(observed.tools) or 'ninguna'})")
    any_of = expected.get("herramientas_alguna")
    if any_of and not called.intersection(any_of):
        failures.append(f"no llamó ninguna de {', '.join(any_of)} (llamó: {', '.join(observed.tools) or 'ninguna'})")
    forbidden = sorted(called.intersection(expected.get("no_debe_llamar", [])))
    if forbidden:
        failures.append(f"llamó {', '.join(forbidden)} y no debía")

    if "ventas" in expected:
        want = _sum_by_sku((row["sku"], int(row["cantidad"])) for row in expected["ventas"])
        got = _sum_by_sku(observed.sales)
        if want != got:
            failures.append(f"ventas: esperaba {dict(want) or 'ninguna'}, quedó {dict(got) or 'ninguna'}")

    if "cambios" in expected:
        rows = expected["cambios"]
        # La unidad solo se compara si el caso la fija en todos sus cambios.
        with_unit = bool(rows) and all("unidad" in row for row in rows)
        want_changes = sorted(
            (row["sku"], row["accion"], int(row["cantidad"])) + ((row["unidad"],) if with_unit else ())
            for row in rows
        )
        got_changes = sorted(change if with_unit else change[:3] for change in observed.changes)
        if want_changes != got_changes:
            failures.append(f"cambios: esperaba {want_changes or 'ninguno'}, quedó {got_changes or 'ninguno'}")

    if "conteo" in expected:
        got_count = dict(observed.count)
        for row in expected["conteo"]:
            tolerance = int(row.get("tolerancia", 0))
            value = got_count.get(row["sku"])
            if value is None or abs(value - int(row["cantidad"])) > tolerance:
                failures.append(f"conteo {row['sku']}: esperaba {row['cantidad']}±{tolerance}, quedó {value}")

    reply = _fold(observed.reply)
    for needle in expected.get("respuesta_contiene", []):
        if not any(_fold(option) in reply for option in needle.split("|")):
            failures.append(f"la respuesta no dice '{needle}'")
    for needle in expected.get("respuesta_no_contiene", []):
        if _fold(needle) in reply:
            failures.append(f"la respuesta dice '{needle}' y no debía")

    if not observed.finished:
        failures.append("se acabaron las vueltas sin respuesta final")
    return failures


@dataclass
class CaseResult:
    case: dict
    status: str  # "paso", "fallo", "error" u "omitido"
    failures: list[str]
    observed: Observed
    usage: TaskUsage
    seconds: float


def media_path(case: dict, base: Path) -> Path | None:
    entry = case.get("entrada") or {}
    name = entry.get("audio") or entry.get("foto")
    return base / name if name else None


async def run_case(case: dict, store: dict, base: Path) -> CaseResult:
    entry = case.get("entrada") or {}
    expected = case.get("esperado") or {}
    observed = Observed()
    path = media_path(case, base)
    if path is not None and not path.is_file():
        usage = TaskUsage(task=case["id"])
        return CaseResult(case, "omitido", [f"falta el archivo {path.name}"], observed, usage, 0.0)

    started = time.perf_counter()
    with track_task(case["id"]) as usage:
        try:
            if entry.get("foto"):
                mime = MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")
                lines = await extract_stock(store["catalogo"], path.read_bytes(), mime, note=entry.get("nota", ""))
                observed.count = [(str(line.sku), line.quantity) for line in match_stock_lines(lines, store["catalogo"])]
            else:
                text = entry.get("texto", "")
                if entry.get("audio"):
                    mime = MEDIA_TYPES.get(path.suffix.lower(), "audio/ogg")
                    text = await transcribe_audio(path.read_bytes(), mime)
                    observed.transcript = text
                contexto = case.get("contexto") or {}
                actions = fixed_store_actions(store, observed, order_open=bool(contexto.get("pedido_abierto")))
                result = await run_agent(text, build_tools(actions, user_message=text), AGENT_SYSTEM_PROMPT)
                observed.tools = [call.name for call in result.tool_calls]
                observed.reply = result.reply
                observed.finished = result.finished
        except Exception as exc:
            seconds = time.perf_counter() - started
            return CaseResult(case, "error", [f"{type(exc).__name__}: {exc}"], observed, usage, seconds)
    seconds = time.perf_counter() - started
    failures = evaluate(expected, observed)
    return CaseResult(case, "fallo" if failures else "paso", failures, observed, usage, seconds)
