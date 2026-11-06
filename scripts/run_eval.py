"""Corre la evaluación propia contra Grok (pasarela del reto) y escribe los resultados.

    python -m scripts.run_eval                      # todos los casos
    python -m scripts.run_eval --solo V01,V14       # algunos casos
    python -m scripts.run_eval --categoria pedido   # una categoría

Deja en eval/resultados/<fecha>/:
- resultados.jsonl: un renglón por caso (qué hizo el agente, si pasó, tokens, costo, latencia).
- resumen.md: acierto por categoría, costo por tarea resuelta y la lista de fallos.
- resumen.json: los mismos números para el reporte.

Los casos con audio o foto que todavía no tienen archivo quedan como "omitido" (no cuentan).
"""

import argparse
import asyncio
import json
import statistics
from datetime import datetime
from pathlib import Path

from app.config import get_settings
from app.evaluation import CaseResult, run_case

EVAL_DIR = Path("eval")


def load_cases(path: Path, only: set[str], category: str | None) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [case["id"] for case in cases]
    duplicated = {case_id for case_id in ids if ids.count(case_id) > 1}
    if duplicated:
        raise SystemExit(f"Casos con id repetido: {', '.join(sorted(duplicated))}")
    if only:
        cases = [case for case in cases if case["id"] in only]
    if category:
        cases = [case for case in cases if case["categoria"] == category]
    return cases


def result_row(result: CaseResult) -> dict:
    usage = result.usage
    entry = result.case.get("entrada") or {}
    return {
        "id": result.case["id"],
        "categoria": result.case["categoria"],
        "origen": result.case.get("origen", "redactado"),
        "entrada": entry.get("texto") or entry.get("audio") or entry.get("foto"),
        "estado": result.status,
        "fallas": result.failures,
        "herramientas": result.observed.tools,
        "ventas": result.observed.sales,
        "cambios": result.observed.changes,
        "conteo": result.observed.count,
        "transcripcion": result.observed.transcript,
        "respuesta": result.observed.reply,
        "llamadas": len(usage.calls),
        "tokens_entrada": usage.prompt_tokens,
        "tokens_cache": usage.cached_tokens,
        "tokens_salida": usage.completion_tokens,
        "costo_ticks": usage.cost_ticks,
        "costo_usd": usage.cost_usd,
        "costo_completo": usage.cost_complete,
        "segundos": round(result.seconds, 2),
    }


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return ordered[index]


def summarize(rows: list[dict], model: str, trm: float, messages_per_day: int) -> dict:
    ran = [row for row in rows if row["estado"] != "omitido"]
    passed = [row for row in ran if row["estado"] == "paso"]
    total_usd = sum(row["costo_usd"] for row in ran)
    seconds = [row["segundos"] for row in ran]
    per_task = total_usd / len(ran) if ran else 0.0
    per_resolved = total_usd / len(passed) if passed else 0.0

    categories: dict[str, dict] = {}
    for row in ran:
        bucket = categories.setdefault(row["categoria"], {"casos": 0, "pasaron": 0, "costo_usd": 0.0, "segundos": 0.0})
        bucket["casos"] += 1
        bucket["pasaron"] += row["estado"] == "paso"
        bucket["costo_usd"] += row["costo_usd"]
        bucket["segundos"] += row["segundos"]
    for bucket in categories.values():
        bucket["acierto"] = bucket["pasaron"] / bucket["casos"]
        bucket["costo_promedio_usd"] = bucket["costo_usd"] / bucket["casos"]
        bucket["segundos_promedio"] = bucket["segundos"] / bucket["casos"]

    return {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "modelo": model,
        "casos": len(rows),
        "ejecutados": len(ran),
        "omitidos": len(rows) - len(ran),
        "pasaron": len(passed),
        "errores": sum(row["estado"] == "error" for row in ran),
        "acierto": len(passed) / len(ran) if ran else 0.0,
        "por_origen": {
            origin: sum(row["origen"] == origin for row in ran) for origin in sorted({row["origen"] for row in ran})
        },
        "costo_total_usd": total_usd,
        "costo_por_tarea_usd": per_task,
        "costo_por_tarea_resuelta_usd": per_resolved,
        "costo_incompleto": [row["id"] for row in ran if not row["costo_completo"]],
        "tokens_promedio": {
            key: statistics.mean(row[key] for row in ran) if ran else 0
            for key in ("tokens_entrada", "tokens_cache", "tokens_salida", "llamadas")
        },
        "segundos_p50": percentile(seconds, 0.5),
        "segundos_p95": percentile(seconds, 0.95),
        "trm": trm,
        "mensajes_por_dia": messages_per_day,
        "costo_mensual_tienda_usd": per_task * messages_per_day * 30,
        "por_categoria": dict(sorted(categories.items())),
    }


def usd(value: float) -> str:
    return f"US${value:.5f}"


def cop(value_usd: float, trm: float) -> str:
    return f"${value_usd * trm:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


def summary_markdown(summary: dict, rows: list[dict]) -> str:
    trm = summary["trm"]
    lines = [
        "# Evaluación de TenderoBot",
        "",
        f"- Fecha: {summary['fecha']}",
        f"- Modelo: `{summary['modelo']}` por la pasarela del reto",
        f"- Casos: {summary['ejecutados']} ejecutados, {summary['omitidos']} omitidos (sin archivo)",
        "- Origen de los casos ejecutados: "
        + ", ".join(f"{count} {origin}" for origin, count in summary["por_origen"].items()),
        "",
        f"## Resultado: {summary['pasaron']} de {summary['ejecutados']} ({summary['acierto']:.0%})",
        "",
        "| Categoría | Casos | Pasaron | Acierto | Costo promedio | Segundos promedio |",
        "|---|---|---|---|---|---|",
    ]
    for name, bucket in summary["por_categoria"].items():
        lines.append(
            f"| {name} | {bucket['casos']} | {bucket['pasaron']} | {bucket['acierto']:.0%} | "
            f"{usd(bucket['costo_promedio_usd'])} | {bucket['segundos_promedio']:.1f} |"
        )
    tokens = summary["tokens_promedio"]
    lines += [
        "",
        "## Costo",
        "",
        f"Costo reportado por la pasarela en cada respuesta (`cost_in_usd_ticks`). Pesos con TRM "
        f"{trm:,.0f}".replace(",", ".") + " (`--trm`).",
        "",
        "| Medida | USD | COP |",
        "|---|---|---|",
        f"| Total de la corrida | {usd(summary['costo_total_usd'])} | {cop(summary['costo_total_usd'], trm)} |",
        f"| Por tarea (promedio) | {usd(summary['costo_por_tarea_usd'])} | {cop(summary['costo_por_tarea_usd'], trm)} |",
        f"| **Por tarea resuelta** (total ÷ casos que pasaron) | {usd(summary['costo_por_tarea_resuelta_usd'])} | "
        f"{cop(summary['costo_por_tarea_resuelta_usd'], trm)} |",
        f"| Por tienda al mes ({summary['mensajes_por_dia']} mensajes al día) | {usd(summary['costo_mensual_tienda_usd'])} | "
        f"{cop(summary['costo_mensual_tienda_usd'], trm)} |",
        "",
        f"Promedio por tarea: {tokens['llamadas']:.1f} llamadas, {tokens['tokens_entrada']:.0f} tokens de entrada "
        f"({tokens['tokens_cache']:.0f} en caché) y {tokens['tokens_salida']:.0f} de salida. "
        f"Latencia: p50 {summary['segundos_p50']:.1f} s, p95 {summary['segundos_p95']:.1f} s.",
    ]
    if summary["costo_incompleto"]:
        lines.append(
            f"\nSin costo reportado (transcripción de audio): {', '.join(summary['costo_incompleto'])}."
        )
    failed = [row for row in rows if row["estado"] in ("fallo", "error")]
    lines += ["", f"## Fallos ({len(failed)})", ""]
    if failed:
        lines += ["| Caso | Categoría | Entrada | Qué falló | Respuesta del agente |", "|---|---|---|---|---|"]
        for row in failed:
            reply = (row["respuesta"] or "").replace("\n", " ").replace("|", "/")
            lines.append(
                f"| {row['id']} | {row['categoria']} | {row['entrada']} | {'; '.join(row['fallas'])} | {reply} |"
            )
    else:
        lines.append("Ninguno.")
    skipped = [row for row in rows if row["estado"] == "omitido"]
    if skipped:
        lines += ["", "## Omitidos", ""] + [f"- {row['id']}: {'; '.join(row['fallas'])}" for row in skipped]
    return "\n".join(lines) + "\n"


async def run_all(cases: list[dict], store: dict, concurrency: int) -> list[CaseResult]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case: dict) -> CaseResult:
        async with semaphore:
            result = await run_case(case, store, EVAL_DIR)
            print(f"{case['id']:>4} {result.status:<8} US${result.usage.cost_usd:.5f} {result.seconds:5.1f}s "
                  + ("; ".join(result.failures) if result.status != "paso" else ""), flush=True)
            return result

    return list(await asyncio.gather(*(one(case) for case in cases)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--casos", type=Path, default=EVAL_DIR / "casos.jsonl")
    parser.add_argument("--tienda", type=Path, default=EVAL_DIR / "tienda.json")
    parser.add_argument("--solo", default="", help="ids separados por coma")
    parser.add_argument("--categoria", default=None)
    parser.add_argument("--concurrencia", type=int, default=4)
    parser.add_argument("--trm", type=float, default=4000.0, help="pesos por dólar para mostrar el costo en COP")
    parser.add_argument("--mensajes-dia", type=int, default=20, help="mensajes por tienda al día (proyección)")
    parser.add_argument("--salida", type=Path, default=EVAL_DIR / "resultados")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.grok_api_key:
        raise SystemExit("Falta GROK_API_KEY en .env: la evaluación corre contra la pasarela del reto.")
    only = {item.strip() for item in args.solo.split(",") if item.strip()}
    cases = load_cases(args.casos, only, args.categoria)
    if not cases:
        raise SystemExit("No hay casos para correr.")
    store = json.loads(args.tienda.read_text(encoding="utf-8"))

    results = asyncio.run(run_all(cases, store, max(1, args.concurrencia)))
    rows = [result_row(result) for result in results]
    summary = summarize(rows, settings.grok_model, args.trm, args.mensajes_dia)

    out = args.salida / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "resultados.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    (out / "resumen.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "resumen.md").write_text(summary_markdown(summary, rows), encoding="utf-8")
    print(
        f"\n{summary['pasaron']}/{summary['ejecutados']} pasaron ({summary['acierto']:.0%}) · "
        f"costo por tarea resuelta {usd(summary['costo_por_tarea_resuelta_usd'])} · resultados en {out}"
    )


if __name__ == "__main__":
    main()
