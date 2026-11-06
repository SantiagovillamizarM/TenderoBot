"""Costo por tarea resuelta, por tipo de tarea, y proyección por tienda (S3, punto 4.3).

    python -m scripts.cost_report                     # últimas 3 corridas de la evaluación
    python -m scripts.cost_report --medir-foto 3      # además mide el costo de 3 fotos contra Grok

Lee los resultados de la evaluación (eval/resultados/<fecha>/resultados.jsonl), agrupa los casos
por tipo de tarea y calcula el costo por tarea resuelta: todo lo gastado (también en los casos que
fallaron) dividido por los casos que pasaron. Con una mezcla de mensajes por día proyecta el costo
mensual por tienda.

La foto se mide aparte porque todavía no hay fotos reales en la evaluación: el costo de una imagen
depende de su resolución, no de lo que muestra, así que se usa una imagen generada del tamaño en
que Telegram entrega las fotos (1280x960). La transcripción de audio no se puede costear: la
pasarela no reporta su costo.

Escribe eval/resultados/COSTOS.md y costos.json.
"""

import argparse
import asyncio
import io
import json
import random
import statistics
from pathlib import Path

from app.ai.grok_client import extract_stock
from app.ai.usage import track_task
from app.config import get_settings

RESULTS_DIR = Path("eval/resultados")
TASK_TYPES = {
    "ventas": ("ventas_texto", "ventas_dificil", "ventas_voz"),
    "pedido": ("pedido", "pedido_dificil", "pedido_voz"),
    "consulta": ("consulta", "consulta_dificil"),
    "conversacion": ("conversacion", "fuera_de_tema", "seguridad"),
    "foto": ("foto",),
}
# Mensajes por tienda al día. Supuesto hasta tener la bitácora de uso real (tabla llm_usage).
DEFAULT_MIX = {"ventas": 12, "consulta": 3, "pedido": 2, "conversacion": 2, "foto": 1}
TELEGRAM_PHOTO_SIZE = (1280, 960)


def task_type(category: str) -> str | None:
    return next((name for name, categories in TASK_TYPES.items() if category in categories), None)


def load_rows(runs: list[Path]) -> list[dict]:
    rows = []
    for run in runs:
        for line in (run / "resultados.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row["estado"] != "omitido":
                rows.append({**row, "corrida": run.name})
    return rows


def type_stats(rows: list[dict]) -> dict:
    passed = [row for row in rows if row["estado"] == "paso"]
    costs = [row["costo_usd"] for row in rows]
    return {
        "tareas": len(rows),
        "pasaron": len(passed),
        "acierto": len(passed) / len(rows),
        "costo_promedio_usd": statistics.mean(costs),
        "costo_max_usd": max(costs),
        "costo_por_tarea_resuelta_usd": sum(costs) / len(passed) if passed else None,
        "llamadas_promedio": statistics.mean(row["llamadas"] for row in rows),
        "tokens_entrada_promedio": statistics.mean(row["tokens_entrada"] for row in rows),
        "tokens_cache_promedio": statistics.mean(row["tokens_cache"] for row in rows),
        "tokens_salida_promedio": statistics.mean(row["tokens_salida"] for row in rows),
        "segundos_promedio": statistics.mean(row["segundos"] for row in rows),
    }


def synthetic_photo() -> bytes:
    """Imagen con ruido del tamaño de una foto de Telegram (para medir costo, no acierto)."""
    from PIL import Image

    rng = random.Random(7)
    width, height = TELEGRAM_PHOTO_SIZE
    image = Image.new("RGB", (width // 8, height // 8))
    image.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(image.width * image.height)])
    buffer = io.BytesIO()
    image.resize((width, height)).save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


async def measure_photo(times: int, catalog: list[dict]) -> list[dict]:
    photo = synthetic_photo()
    rows = []
    for index in range(times):
        with track_task(f"foto-{index}") as usage:
            try:
                await extract_stock(catalog, photo, "image/jpeg")
            except Exception as exc:  # una foto ilegible igual cuesta: se registra el gasto
                print(f"foto {index}: {type(exc).__name__}")
        rows.append(
            {
                "id": f"foto-{index}",
                "categoria": "foto",
                # Solo se mide el costo: el acierto con fotos se medirá con F01-F03 reales.
                "estado": "paso",
                "costo_usd": usage.cost_usd,
                "llamadas": len(usage.calls),
                "tokens_entrada": usage.prompt_tokens,
                "tokens_cache": usage.cached_tokens,
                "tokens_salida": usage.completion_tokens,
                "segundos": usage.latency_ms / 1000,
            }
        )
    return rows


def number(value: float, decimals: int) -> str:
    """Número en formato colombiano: 2.351,46"""
    return f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def money(value_usd: float | None, trm: float) -> tuple[str, str]:
    if value_usd is None:
        return "—", "—"
    usd_decimals = 5 if value_usd < 1 else 2
    cop_decimals = 2 if value_usd * trm < 1000 else 0
    return f"US${number(value_usd, usd_decimals)}", f"${number(value_usd * trm, cop_decimals)}"


def build_report(by_type: dict, mix: dict, trm: float, runs: list[str], photo_measured: bool) -> tuple[dict, str]:
    daily_usd = sum(
        mix[name] * (by_type[name]["costo_por_tarea_resuelta_usd"] or 0) for name in mix if name in by_type
    )
    missing = [name for name in mix if name not in by_type]
    monthly_usd = daily_usd * 30
    data = {
        "corridas": runs,
        "trm": trm,
        "mezcla_diaria": mix,
        "por_tipo": by_type,
        "tipos_sin_medir": missing,
        "costo_diario_tienda_usd": daily_usd,
        "costo_mensual_tienda_usd": monthly_usd,
    }

    lines = [
        "# Costo por tarea",
        "",
        f"Corridas usadas: {', '.join(f'`{run}`' for run in runs)} (configuración final, 3 repeticiones).",
        f"Costo reportado por la pasarela del reto en cada respuesta. Pesos con TRM ${number(trm, 2)} "
        "(3 de noviembre de 2026; cambiar con `--trm`).",
        "",
        "**Costo por tarea resuelta** = todo lo gastado en ese tipo de tarea, también en los intentos que fallaron, "
        "dividido por las tareas que salieron bien.",
        "",
        "| Tipo de tarea | Tareas | Acierto | Costo promedio | Costo máximo | Costo por tarea resuelta (USD) | (COP) | "
        "Llamadas | Tokens entrada (caché) / salida | Segundos |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, stats in by_type.items():
        average_usd, _ = money(stats["costo_promedio_usd"], trm)
        max_usd, _ = money(stats["costo_max_usd"], trm)
        resolved_usd, resolved_cop = money(stats["costo_por_tarea_resuelta_usd"], trm)
        accuracy = "solo costo" if name == "foto" and photo_measured else f"{stats['acierto']:.0%}"
        lines.append(
            f"| {name} | {stats['tareas']} | {accuracy} | {average_usd} | {max_usd} | {resolved_usd} | {resolved_cop} | "
            f"{number(stats['llamadas_promedio'], 1)} | {number(stats['tokens_entrada_promedio'], 0)} "
            f"({number(stats['tokens_cache_promedio'], 0)}) / {number(stats['tokens_salida_promedio'], 0)} | "
            f"{number(stats['segundos_promedio'], 1)} |"
        )
    daily = money(daily_usd, trm)
    monthly = money(monthly_usd, trm)
    lines += [
        "",
        "## Proyección por tienda",
        "",
        "Mezcla supuesta de mensajes por tienda al día (se reemplaza por la real cuando la bitácora `llm_usage` "
        "tenga uso de tiendas de verdad):",
        "",
        "| Tipo | Mensajes al día |",
        "|---|---|",
    ]
    lines += [f"| {name} | {count} |" for name, count in mix.items()]
    lines += [
        "",
        "| | USD | COP |",
        "|---|---|---|",
        f"| Por tienda al día | {daily[0]} | {daily[1]} |",
        f"| **Por tienda al mes** (30 días) | **{monthly[0]}** | **{monthly[1]}** |",
        f"| 100 tiendas al mes | {money(monthly_usd * 100, trm)[0]} | {money(monthly_usd * 100, trm)[1]} |",
        f"| 1.000 tiendas al mes | {money(monthly_usd * 1000, trm)[0]} | {money(monthly_usd * 1000, trm)[1]} |",
        "",
        "## Lo que no está en estos números",
        "",
        "- **Transcripción de audio:** la pasarela no reporta su costo ni lo publica en `/models`. Cada mensaje de voz "
        "cuesta lo de su texto más una transcripción sin costear.",
    ]
    if photo_measured:
        lines.append(
            "- **Foto:** costo medido con una imagen generada de 1280×960 (el tamaño de Telegram). El costo de la "
            "imagen depende de la resolución, no del contenido; la respuesta de una foto real con muchos productos "
            "trae más tokens de salida. El acierto con fotos se medirá con los casos F01–F03 reales."
        )
    if missing:
        lines.append(f"- **Sin medir:** {', '.join(missing)} (no entran en la proyección).")
    lines.append(
        "- **Caché:** el costo bajo depende de que la pasarela tenga las instrucciones en caché (más del 80% de los "
        "tokens de entrada). Con poco tráfico la caché se pierde y cada tarea cuesta hasta unas 3 veces más: es la "
        "diferencia entre el costo promedio y el máximo de la tabla."
    )
    return data, "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corridas", nargs="*", type=Path, help="carpetas de resultados (por defecto, las 3 últimas)")
    parser.add_argument("--trm", type=float, default=3182.99, help="pesos por dólar (TRM del 3/11/2026)")
    parser.add_argument("--medir-foto", type=int, default=0, help="cuántas fotos medir contra Grok")
    for name, count in DEFAULT_MIX.items():
        parser.add_argument(f"--{name}", type=int, default=count, help=f"mensajes de {name} al día por tienda")
    args = parser.parse_args()

    runs = args.corridas or sorted(path for path in RESULTS_DIR.iterdir() if (path / "resultados.jsonl").is_file())[-3:]
    rows = load_rows(runs)
    if args.medir_foto:
        if not get_settings().grok_api_key:
            raise SystemExit("Falta GROK_API_KEY en .env para medir la foto.")
        catalog = json.loads(Path("eval/tienda.json").read_text(encoding="utf-8"))["catalogo"]
        rows += asyncio.run(measure_photo(args.medir_foto, catalog))

    grouped: dict[str, list[dict]] = {}
    for row in rows:
        name = task_type(row["categoria"])
        if name is not None:
            grouped.setdefault(name, []).append(row)
    by_type = {name: type_stats(grouped[name]) for name in TASK_TYPES if name in grouped}
    mix = {name: getattr(args, name) for name in DEFAULT_MIX}
    data, markdown = build_report(by_type, mix, args.trm, [run.name for run in runs], bool(args.medir_foto))
    (RESULTS_DIR / "costos.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (RESULTS_DIR / "COSTOS.md").write_text(markdown, encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
