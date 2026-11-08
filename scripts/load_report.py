"""Bitácora completa de una corrida de carga (S4).

    python -m scripts.load_report --corrida grande

Escribe en carga/resultados/<corrida>/:
- resumen.md y resumen.json: volumen, tasa de éxito, tokens, costo, errores clasificados con
  ejemplos, continuidad (huecos) y una fila por hora.
- unidades.jsonl y unidades.csv: cada unidad con su resultado, consumo y error.
- llamadas.csv: cada llamada a Grok (de la bitácora llm_usage), también las de reintentos.
- por_hora.csv y senales.csv: el resumen por hora y las señales de vida.

Solo lee la base de carga (LOAD_DATABASE_URL); no cambia nada.
"""

import argparse
import asyncio
import csv
import json
from datetime import datetime
from pathlib import Path

from app.load.database import LoadDatabaseError, use_load_database

OUT_DIR = Path("carga/resultados")
TASK_FIELDS = [
    "position", "unit_id", "store_index", "kind", "origin", "text", "status", "attempts", "started_at",
    "finished_at", "latency_ms", "tools", "observed", "reply", "failures", "confirmed", "error_type",
    "error_message", "llm_calls", "prompt_tokens", "cached_tokens", "completion_tokens", "cost_ticks", "expected",
]


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _cell(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=_json_default)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _cell(row.get(key)) for key in fields})


def _num(value: float, decimals: int = 0) -> str:
    return f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(value: float, decimals: int = 1) -> str:
    return f"{_num(value * 100, decimals)}%"


def _usd(value: float | None) -> str:
    return "—" if value is None else f"US${_num(value, 5 if value < 1 else 2)}"


def summary_markdown(summary: dict, trm: float) -> str:
    total = summary["total"]
    lines = [
        f"# Corrida de carga «{summary['corrida']}»",
        "",
        f"- Estado: **{summary['estado']}** · de {summary['inicio']:%Y-%m-%d %H:%M} a "
        f"{summary['fin']:%Y-%m-%d %H:%M} UTC (**{_num(summary['horas'], 2)} h**)" if summary["fin"] else
        f"- Estado: **{summary['estado']}** · sin unidades terminadas",
        f"- Unidades: **{total['unidades']}** terminadas de {summary['total_unidades']} "
        f"({summary['pendientes']} pendientes) · {_num(summary['unidades_por_hora'], 1)} por hora",
        "- Origen: " + ", ".join(f"{count} {origin}" for origin, count in summary["origen"].items()),
        f"- Ventas confirmadas (movieron el inventario): {summary['ventas_confirmadas']}",
        "",
        f"## Resultado: {total['exito']} de {total['unidades']} bien ({_pct(total['tasa_exito'])})",
        "",
        f"Fallos (el agente respondió mal): {total['fallo']} · errores (no se completó): {total['error']} · "
        f"reintentos: {total['reintentos']}",
        "",
        "| Tipo | Unidades | Éxito | Fallo | Error | Costo por unidad | p50 | p95 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for kind, block in summary["por_tipo"].items():
        lines.append(
            f"| {kind} | {block['unidades']} | {_pct(block['tasa_exito'])} | {block['fallo']} | {block['error']} | "
            f"{_usd(block['costo_por_unidad_usd'])} | {_num(block['segundos_p50'], 1)} s | {_num(block['segundos_p95'], 1)} s |"
        )
    lines += [
        "",
        "## Consumo",
        "",
        "| | Valor |",
        "|---|---|",
        f"| Llamadas a Grok | {_num(total['llamadas'])} |",
        f"| Tokens de entrada (en caché) | {_num(total['tokens_entrada'])} ({_num(total['tokens_cache'])}, "
        f"{_pct(total['porcentaje_cache'], 0)}) |",
        f"| Tokens de salida | {_num(total['tokens_salida'])} |",
        f"| **Costo total** | **{_usd(total['costo_usd'])}** (${_num(total['costo_usd'] * trm)} COP) |",
        f"| Costo por unidad | {_usd(total['costo_por_unidad_usd'])} |",
        f"| Costo por unidad resuelta | {_usd(total['costo_por_exito_usd'])} |",
        f"| Latencia p50 / p95 | {_num(total['segundos_p50'], 1)} s / {_num(total['segundos_p95'], 1)} s |",
        "",
        f"Costo reportado por la pasarela en cada respuesta, incluidos los reintentos. COP con TRM {_num(trm, 2)}.",
        "",
        "## Fallos y errores",
        "",
    ]
    if summary["categorias"]:
        lines += ["| Categoría | Unidades |", "|---|---|"]
        lines += [f"| {category} | {count} |" for category, count in summary["categorias"].items()]
        for category, examples in summary["ejemplos"].items():
            lines += ["", f"**{category}**", ""]
            for example in examples:
                detail = f" — {example['detalle']}" if example["detalle"] else ""
                reply = f" · respuesta: «{example['respuesta']}»" if example["respuesta"] else ""
                lines.append(f"- {example['unidad']}: «{example['texto']}»{detail}{reply}")
    else:
        lines.append("Ninguno.")
    lines += ["", "## Continuidad", ""]
    lines.append(
        f"{summary['senales_de_vida']} señales de vida; el mayor hueco entre dos señales fue de "
        f"{_num(summary['mayor_hueco_senales_min'], 1)} min. Freno por errores de pasarela: "
        f"{summary['freno_activado']} veces."
    )
    starts, abrupt = summary.get("arranques", 0), summary.get("cortes_bruscos", 0)
    if starts > 1:
        lines.append(
            f"Arranques: {starts} (la corrida se retomó {starts - 1} veces con el mismo comando; "
            f"{abrupt} de ellas tras un corte brusco, sin cierre ordenado). Ninguna unidad se repitió ni se perdió: "
            "las que quedaban a medias volvieron a la cola y su intento quedó contado."
        )
    else:
        lines.append("Arranques: 1 (corrida de un solo tirón, sin reinicios).")
    if summary["huecos"]:
        lines.append("")
        lines.append("Cortes (tiempo largo sin terminar unidades):")
        lines += [f"- {gap['desde']:%Y-%m-%d %H:%M} → {gap['hasta']:%H:%M} UTC ({_num(gap['minutos'], 1)} min)"
                  for gap in summary["huecos"]]
    else:
        lines.append("Sin cortes: ninguna pausa larga entre unidades.")
    lines += ["", "## Por hora", "", "| Hora | Desde (Colombia) | Unidades | Éxito | Errores | Costo | Caché | p95 |",
              "|---|---|---|---|---|---|---|---|"]
    for row in summary["por_hora"]:
        lines.append(
            f"| {row['hora']} | {row['desde_colombia']} | {row['unidades']} | {_pct(row['tasa_exito'], 0)} | {row['error']} | "
            f"{_usd(row['costo_usd'])} | {_pct(row['porcentaje_cache'], 0)} | {_num(row['segundos_p95'], 1)} s |"
        )
    return "\n".join(lines) + "\n"


async def collect(run_name: str) -> tuple[dict, list[dict], list[dict], list[dict]]:
    from sqlalchemy import or_, select

    from app.db import SessionLocal, engine
    from app.load.models import LoadHeartbeat, LoadRun, LoadTask
    from app.models import LlmUsage

    try:
        async with SessionLocal() as session:
            run = await session.scalar(select(LoadRun).where(LoadRun.name == run_name))
            if run is None:
                raise SystemExit(f"No existe la corrida «{run_name}».")
            tasks = (await session.scalars(
                select(LoadTask).where(LoadTask.run_id == run.id).order_by(LoadTask.position)
            )).all()
            beats = (await session.scalars(
                select(LoadHeartbeat).where(LoadHeartbeat.run_id == run.id).order_by(LoadHeartbeat.id)
            )).all()
            calls = (await session.scalars(
                # task_id = r<corrida>-<unidad>; las primeras pruebas lo guardaban en `task`.
                select(LlmUsage)
                .where(or_(LlmUsage.task_id.like(f"r{run.id}-%"), LlmUsage.task.like(f"r{run.id}-%")))
                .order_by(LlmUsage.id)
            )).all()
    finally:
        await engine.dispose()
    as_dict = lambda obj: {c.key: getattr(obj, c.key) for c in obj.__mapper__.column_attrs}  # noqa: E731
    return as_dict(run), [as_dict(t) for t in tasks], [as_dict(b) for b in beats], [as_dict(c) for c in calls]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corrida", required=True)
    parser.add_argument("--trm", type=float, default=3182.99)
    args = parser.parse_args()
    try:
        use_load_database()
    except LoadDatabaseError as exc:
        raise SystemExit(str(exc)) from exc

    from app.load.logbook import summarize

    run, tasks, beats, calls = asyncio.run(collect(args.corrida))
    summary = summarize(tasks, run, beats)
    out = OUT_DIR / args.corrida
    out.mkdir(parents=True, exist_ok=True)
    (out / "unidades.jsonl").write_text(
        "".join(json.dumps(t, ensure_ascii=False, default=_json_default) + "\n" for t in tasks), encoding="utf-8"
    )
    write_csv(out / "unidades.csv", tasks, TASK_FIELDS)
    write_csv(out / "llamadas.csv", calls, ["id", "created_at", "task_id", "task", "outcome", "store_id", "endpoint", "model",
                                            "prompt_tokens", "cached_tokens", "completion_tokens", "cost_ticks",
                                            "cost_estimated", "latency_ms", "ok"])
    write_csv(out / "senales.csv", beats, ["id", "at", "done", "pending", "success", "failed", "errors", "cost_ticks", "note"])
    write_csv(out / "por_hora.csv", summary["por_hora"], list(summary["por_hora"][0]) if summary["por_hora"] else ["hora"])
    (out / "resumen.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8"
    )
    (out / "resumen.md").write_text(summary_markdown(summary, args.trm), encoding="utf-8")
    total = summary["total"]
    print(f"{total['unidades']} unidades · éxito {_pct(total['tasa_exito'])} · {total['error']} errores · "
          f"{_usd(total['costo_usd'])} · {len(calls)} llamadas · bitácora en {out}")


if __name__ == "__main__":
    main()
