"""Bitácora de la semana de carga (S4): clasifica fallos y resume la corrida.

Trabaja con diccionarios (una fila de load_tasks por unidad), sin base de datos, para poder probarlo.
scripts/load_report.py lee la base, llama a estas funciones y escribe los archivos.

Categorías de una unidad que no salió bien:
- error (no se completó tras los reintentos): pasarela, red, tiempo o codigo.
- fallo (el agente respondió pero no hizo lo correcto): alucinacion_resumen, ventas_incorrectas,
  cambios_incorrectos, herramienta_no_usada, herramienta_prohibida, respuesta, sin_terminar.
"""

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from app.ai.usage import TICKS_PER_USD
from app.inventory.engine import COLOMBIA_TZ

DONE = ("exito", "fallo", "error")
# Un hueco entre unidades más largo que esto (o que 4 veces el intervalo) se reporta como corte.
MIN_GAP_SECONDS = 600
CONFIRM_WORDS = re.compile(r"confirm|✅", re.IGNORECASE)


def classify(row: dict) -> str | None:
    """Categoría de la unidad (None si salió bien o sigue pendiente)."""
    status = row.get("status")
    if status == "error":
        error_type = row.get("error_type") or ""
        if error_type.startswith("pasarela_"):
            return "error_pasarela"
        if error_type == "red":
            return "error_red"
        if error_type == "tiempo_unidad":
            return "error_tiempo"
        return "error_codigo"
    if status != "fallo":
        return None
    failures = row.get("failures") or []
    tools = row.get("tools") or []
    observed = row.get("observed") or {}
    expected = row.get("expected") or {}
    # Dijo que había un resumen o un pedido para confirmar sin haber creado ninguno.
    if (expected.get("ventas") and not observed.get("ventas") and "registrar_ventas" not in tools
            and CONFIRM_WORDS.search(row.get("reply") or "")):
        return "alucinacion_resumen"
    if any(f.startswith("se acabaron las vueltas") for f in failures):
        return "sin_terminar"
    if any(f.startswith("ventas:") for f in failures):
        return "ventas_incorrectas"
    if any(f.startswith("cambios:") for f in failures):
        return "cambios_incorrectos"
    if any(f.startswith("no llamó") for f in failures):
        return "herramienta_no_usada"
    if any(f.startswith("llamó") for f in failures):
        return "herramienta_prohibida"
    if any(f.startswith("la respuesta") for f in failures):
        return "respuesta"
    return "otro"


def sales_detail(row: dict) -> str | None:
    """Qué salió mal en las ventas: producto que faltó, que sobró o cantidad distinta."""
    want = Counter()
    for item in (row.get("expected") or {}).get("ventas", []):
        want[item["sku"]] += int(item["cantidad"])
    got = Counter()
    for sku, quantity in (row.get("observed") or {}).get("ventas", []):
        got[sku] += int(quantity)
    if want == got:
        return None
    parts = []
    for sku in sorted(set(want) | set(got)):
        if sku not in got:
            parts.append(f"faltó {sku}")
        elif sku not in want:
            parts.append(f"sobró {sku}")
        elif want[sku] != got[sku]:
            parts.append(f"{sku} {want[sku]}→{got[sku]}")
    return ", ".join(parts)


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))]


def _block(rows: list[dict]) -> dict:
    """Números de un grupo de unidades terminadas."""
    done = [r for r in rows if r.get("status") in DONE]
    success = sum(r["status"] == "exito" for r in done)
    cost = sum(int(r.get("cost_ticks") or 0) for r in done)
    prompt = sum(int(r.get("prompt_tokens") or 0) for r in done)
    cached = sum(int(r.get("cached_tokens") or 0) for r in done)
    latencies = [r["latency_ms"] / 1000 for r in done if r.get("latency_ms") is not None]
    return {
        "unidades": len(done),
        "exito": success,
        "fallo": sum(r["status"] == "fallo" for r in done),
        "error": sum(r["status"] == "error" for r in done),
        "tasa_exito": success / len(done) if done else 0.0,
        "llamadas": sum(int(r.get("llm_calls") or 0) for r in done),
        "tokens_entrada": prompt,
        "tokens_cache": cached,
        "tokens_salida": sum(int(r.get("completion_tokens") or 0) for r in done),
        "porcentaje_cache": cached / prompt if prompt else 0.0,
        "costo_usd": cost / TICKS_PER_USD,
        "costo_por_unidad_usd": cost / TICKS_PER_USD / len(done) if done else 0.0,
        "costo_por_exito_usd": cost / TICKS_PER_USD / success if success else None,
        "reintentos": sum(max(0, int(r.get("attempts") or 0) - 1) for r in done),
        "segundos_p50": _percentile(latencies, 0.5),
        "segundos_p95": _percentile(latencies, 0.95),
    }


def gaps(rows: list[dict], interval: float | None) -> list[dict]:
    """Huecos largos entre una unidad terminada y la siguiente (cortes de la corrida)."""
    limit = max(MIN_GAP_SECONDS, 4 * (interval or 0))
    times = sorted(r["finished_at"] for r in rows if r.get("status") in DONE and r.get("finished_at"))
    found = []
    for before, after in zip(times, times[1:]):
        seconds = (after - before).total_seconds()
        if seconds > limit:
            found.append({"desde": before, "hasta": after, "minutos": round(seconds / 60, 1)})
    return found


def hourly(rows: list[dict], start: datetime) -> list[dict]:
    """Una fila por hora de corrida (hora 0 = la primera hora desde el inicio)."""
    buckets: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("status") in DONE and row.get("finished_at"):
            buckets[int((row["finished_at"] - start).total_seconds() // 3600)].append(row)
    result = []
    for hour in range(max(buckets) + 1 if buckets else 0):
        block = _block(buckets.get(hour, []))
        local = (start + timedelta(hours=hour)).astimezone(COLOMBIA_TZ)
        result.append({"hora": hour, "desde_colombia": local.strftime("%Y-%m-%d %H:%M"), **block})
    return result


def summarize(rows: list[dict], run: dict, heartbeats: list[dict]) -> dict:
    """Resumen de la corrida: volumen, éxito, consumo, errores, continuidad y por hora."""
    done = [r for r in rows if r.get("status") in DONE]
    finished = sorted(r["finished_at"] for r in done if r.get("finished_at"))
    started = min((r["started_at"] for r in done if r.get("started_at")), default=run.get("started_at"))
    end = finished[-1] if finished else None
    hours = (end - started).total_seconds() / 3600 if started and end else 0.0
    interval = (run.get("config") or {}).get("intervalo_s")

    categories = Counter(c for c in (classify(r) for r in rows) if c)
    examples: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        category = classify(row)
        if category and len(examples[category]) < 5:
            examples[category].append({
                "unidad": row.get("unit_id"),
                "texto": row.get("text"),
                "detalle": sales_detail(row) if category == "ventas_incorrectas" else
                (row.get("error_type") or "; ".join(row.get("failures") or [])),
                "respuesta": (row.get("reply") or "")[:200],
            })
    beats = sorted(h["at"] for h in heartbeats)
    beat_gap = max(((b - a).total_seconds() / 60 for a, b in zip(beats, beats[1:])), default=0.0)

    return {
        "corrida": run.get("name"),
        "estado": run.get("status"),
        "inicio": started,
        "fin": end,
        "horas": round(hours, 2),
        "total_unidades": len(rows),
        "pendientes": sum(r.get("status") in ("pendiente", "procesando") for r in rows),
        "unidades_por_hora": round(len(done) / hours, 1) if hours else 0.0,
        "origen": dict(Counter(r.get("origin") for r in done)),
        "total": _block(rows),
        "por_tipo": {kind: _block([r for r in rows if r.get("kind") == kind])
                     for kind in sorted({r.get("kind") for r in rows})},
        "categorias": dict(categories.most_common()),
        "ejemplos": dict(examples),
        "errores_por_tipo": dict(Counter(r.get("error_type") for r in rows if r.get("status") == "error")),
        "huecos": gaps(rows, interval),
        "senales_de_vida": len(heartbeats),
        "mayor_hueco_senales_min": round(beat_gap, 1),
        "freno_activado": sum("freno" in (h.get("note") or "") for h in heartbeats),
        "ventas_confirmadas": sum(bool(r.get("confirmed")) for r in rows),
        "por_hora": hourly(rows, started) if started else [],
    }
