"""Corrida de carga desatendida (S4).

    python -m scripts.load_run crear  --corrida piloto --unidades carga/piloto.jsonl --conteo-inicial 300
    python -m scripts.load_run correr --corrida piloto --horas 0.5
    python -m scripts.load_run estado --corrida piloto

- crear: guarda las unidades como pendientes en la base de carga. Con --conteo-inicial, cada tienda
  "cuenta" su estante antes de empezar (para que las ventas no dejen el inventario en negativo).
- correr: procesa las pendientes, una a la vez, repartidas en --horas (o una cada --intervalo
  segundos). Si se corta (Ctrl+C, caída del PC), volver a correr el mismo comando sigue donde iba.
  Mientras corre, Windows no se suspende.
- estado: cuántas van, éxito, fallos, errores, costo y la última señal de vida.

Todo corre contra LOAD_DATABASE_URL (nunca la base real).
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.load.database import LoadDatabaseError, use_load_database

LOG_DIR = Path("carga/logs")


def keep_awake(on: bool) -> None:
    """Evita que Windows se suspenda durante la corrida (la pantalla sí se puede apagar)."""
    if sys.platform != "win32":
        return
    import ctypes

    es_continuous, es_system_required = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | (es_system_required if on else 0))


def setup_logging(run_name: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{run_name}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(path, encoding="utf-8"), logging.StreamHandler()],
    )
    # httpx anota cada petición: demasiado ruido para 12 horas.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return path


async def create(args) -> None:
    from app.load.units import load_units
    from app.load.worker import create_run

    units = load_units(args.unidades)
    config = {"unidades": str(args.unidades), "total": len(units), "conteo_inicial": args.conteo_inicial}
    run_id = await create_run(args.corrida, units, config, initial_count=args.conteo_inicial)
    print(f"Corrida «{args.corrida}» (id {run_id}) creada con {len(units)} unidades pendientes.")


async def run(args) -> None:
    from sqlalchemy import func, select

    from app.config import get_settings
    from app.db import SessionLocal
    from app.load.models import LoadRun, LoadTask
    from app.load.runner import pace_interval
    from app.load.worker import Worker

    async with SessionLocal() as session:
        run_row = await session.scalar(select(LoadRun).where(LoadRun.name == args.corrida))
        if run_row is None:
            raise SystemExit(f"No existe la corrida «{args.corrida}». Créala con `crear`.")
        total = await session.scalar(select(func.count()).where(LoadTask.run_id == run_row.id))
        if args.intervalo is not None:
            interval = args.intervalo
        else:
            interval = pace_interval(total, args.horas)
        run_row.config = {**run_row.config, "intervalo_s": round(interval, 2), "horas": args.horas,
                          "modelo": get_settings().grok_model, "max": args.max}
        await session.commit()

    log_path = setup_logging(args.corrida)
    logging.getLogger(__name__).info(
        "Corrida «%s»: %s unidades, una cada %.1f s, modelo %s. Bitácora de texto en %s",
        args.corrida, total, interval, get_settings().grok_model, log_path,
    )

    def progress(task, outcome) -> None:
        detail = "; ".join(outcome.failures) if outcome.failures else (outcome.error_type or "")
        logging.getLogger("carga").info("%s %-7s %s | %s %s", task.unit_id, outcome.status, task.kind,
                                         task.text[:60], detail[:120])

    keep_awake(True)
    try:
        status = await Worker(args.corrida, interval, max_units=args.max, on_progress=progress).run()
    finally:
        keep_awake(False)
    print(f"Corrida «{args.corrida}»: {status}.")


async def status(args) -> None:
    from sqlalchemy import func, select

    from app.ai.usage import TICKS_PER_USD
    from app.db import SessionLocal
    from app.load.models import ERROR, FAILED, PENDING, RUNNING, SUCCESS, LoadHeartbeat, LoadRun, LoadTask

    async with SessionLocal() as session:
        run_row = await session.scalar(select(LoadRun).where(LoadRun.name == args.corrida))
        if run_row is None:
            raise SystemExit(f"No existe la corrida «{args.corrida}».")
        counts = dict((await session.execute(
            select(LoadTask.status, func.count()).where(LoadTask.run_id == run_row.id).group_by(LoadTask.status)
        )).all())
        cost = await session.scalar(
            select(func.coalesce(func.sum(LoadTask.cost_ticks), 0)).where(LoadTask.run_id == run_row.id)
        )
        beat = await session.scalar(
            select(LoadHeartbeat).where(LoadHeartbeat.run_id == run_row.id).order_by(LoadHeartbeat.id.desc()).limit(1)
        )
    done = sum(counts.get(state, 0) for state in (SUCCESS, FAILED, ERROR))
    total = done + counts.get(PENDING, 0) + counts.get(RUNNING, 0)
    print(f"Corrida «{run_row.name}» · {run_row.status} · empezó {run_row.started_at} · terminó {run_row.finished_at}")
    print(f"  {done}/{total} hechas · éxito {counts.get(SUCCESS, 0)} · fallo {counts.get(FAILED, 0)} · "
          f"error {counts.get(ERROR, 0)} · pendientes {counts.get(PENDING, 0)}")
    if done:
        print(f"  tasa de éxito {counts.get(SUCCESS, 0) / done:.1%} · costo US${int(cost) / TICKS_PER_USD:.4f} "
              f"(US${int(cost) / TICKS_PER_USD / done:.5f} por unidad)")
    if beat is not None:
        print(f"  última señal de vida: {beat.at} ({beat.note or 'normal'})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="accion", required=True)
    crear = sub.add_parser("crear")
    crear.add_argument("--corrida", required=True)
    crear.add_argument("--unidades", type=Path, required=True)
    crear.add_argument("--conteo-inicial", type=int, default=300)
    correr = sub.add_parser("correr")
    correr.add_argument("--corrida", required=True)
    pace = correr.add_mutually_exclusive_group(required=True)
    pace.add_argument("--horas", type=float, help="repartir todas las unidades en estas horas")
    pace.add_argument("--intervalo", type=float, help="segundos entre una unidad y la siguiente")
    correr.add_argument("--max", type=int, default=None, help="procesar como máximo estas unidades y parar")
    estado = sub.add_parser("estado")
    estado.add_argument("--corrida", required=True)
    args = parser.parse_args()

    try:
        print(f"Base de carga: {use_load_database()}")
    except LoadDatabaseError as exc:
        raise SystemExit(str(exc)) from exc
    action = {"crear": create, "correr": run, "estado": status}[args.accion]

    async def main_task() -> None:
        from app.db import engine

        try:
            await action(args)
        finally:
            await engine.dispose()

    try:
        asyncio.run(main_task())
    except KeyboardInterrupt:
        print("\nDetenida. Para seguir donde iba, vuelve a correr el mismo comando.")


if __name__ == "__main__":
    main()
