"""Trabajador de la semana de carga (S4): procesa las unidades una por una, sin supervisión.

Importa app.db: hay que llamar antes a app.load.database.use_load_database() (lo hace el script
scripts/load_run.py), para que todo caiga en la base de carga y nunca en la real.

Por cada unidad:
1. La marca "procesando" y suma un intento (así, si el proceso se cae, se sabe que lo intentó).
2. Corre el agente con las herramientas del bot sobre la tienda de la unidad, con reintentos ante
   errores de la pasarela, y revisa el resultado contra lo esperado.
3. En UNA transacción: guarda el resultado y, si era una venta correcta, la confirma como lo haría
   el tendero (las ventas mueven el inventario). Si la venta salió mal, el tendero la cancela.
4. Guarda el consumo en la bitácora general (llm_usage).

Cada HEARTBEAT_SECONDS deja una señal de vida con los contadores de la corrida.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update

from app.ai.agent import run_agent
from app.ai.prompts import AGENT_SYSTEM_PROMPT
from app.ai.tools import build_tools
from app.ai.usage import TaskUsage, track_task
from app.bot import handlers
from app.db import SessionLocal
from app.evaluation import Observed, evaluate
from app.inventory.engine import load_store_analysis
from app.load.models import ERROR, FAILED, PENDING, RUNNING, SUCCESS, LoadHeartbeat, LoadRun, LoadTask
from app.load.runner import (
    MAX_ATTEMPTS,
    RETRY_DELAYS,
    UNIT_TIMEOUT,
    BREAKER_PAUSE,
    Breaker,
    classify_error,
    is_gateway_error,
    recording_actions,
    resolve_expected,
)
from app.load.units import WorkUnit
from app.models import Distributor, PendingAction, Product, Sale, StockCount, Store
from app.usage_log import save_usage

# El distribuidor que crea scripts/seed.py en la base de carga.
LOAD_DISTRIBUTOR = "Distribuidora El Barrio"
HEARTBEAT_SECONDS = 300
SALE_KINDS = ("venta", "venta_dificil")

logger = logging.getLogger(__name__)


# --- Preparar una corrida --------------------------------------------------------------


async def load_store_ids() -> list[int]:
    """Ids de las tiendas de la base de carga, en orden: la unidad con tienda=0 es la primera."""
    async with SessionLocal() as session:
        distributor = await session.scalar(select(Distributor).where(Distributor.name == LOAD_DISTRIBUTOR))
        if distributor is None:
            raise RuntimeError("La base de carga no tiene tiendas: corre `python -m scripts.load_db setup`.")
        return list(
            (await session.scalars(select(Store.id).where(Store.distributor_id == distributor.id).order_by(Store.id))).all()
        )


async def create_run(name: str, units: list[WorkUnit], config: dict, initial_count: int = 0) -> int:
    """Crea la corrida con sus unidades pendientes. Con `initial_count`, el tendero "cuenta" su estante
    (un conteo por producto) para que la corrida arranque con inventario y las ventas no lo dejen en negativo."""
    store_ids = await load_store_ids()
    bad = sorted({unit.tienda for unit in units if unit.tienda >= len(store_ids)})
    if bad:
        raise ValueError(f"Hay unidades de tiendas que no existen en la base de carga: {bad}")
    async with SessionLocal() as session, session.begin():
        if await session.scalar(select(LoadRun.id).where(LoadRun.name == name)) is not None:
            raise ValueError(f"Ya existe la corrida «{name}». Para seguirla, córrela sin --crear.")
        run = LoadRun(name=name, status="creada", config=config)
        session.add(run)
        await session.flush()
        session.add_all(
            LoadTask(
                run_id=run.id, position=position, unit_id=unit.id, store_index=unit.tienda, kind=unit.tipo,
                origin=unit.origen, text=unit.texto, expected=unit.esperado, confirm=unit.confirmar,
            )
            for position, unit in enumerate(units)
        )
        if initial_count > 0:
            now = datetime.now(UTC)
            products = (await session.scalars(select(Product.id).where(Product.active.is_(True)))).all()
            session.add_all(
                StockCount(store_id=store_id, product_id=product_id, quantity=initial_count, counted_at=now,
                           source="manual")
                for store_id in store_ids
                for product_id in products
            )
        return run.id


# --- Resultado de una unidad -----------------------------------------------------------


@dataclass
class Outcome:
    status: str
    observed: Observed = field(default_factory=Observed)
    failures: list[str] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None


def _usage_fields(usage: TaskUsage) -> dict:
    return {
        "llm_calls": len(usage.calls),
        "prompt_tokens": usage.prompt_tokens,
        "cached_tokens": usage.cached_tokens,
        "completion_tokens": usage.completion_tokens,
        "cost_ticks": usage.cost_ticks,
    }


class Worker:
    def __init__(
        self,
        run_name: str,
        interval: float,
        max_units: int | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        on_progress: Callable[[LoadTask, Outcome], None] | None = None,
    ) -> None:
        self.run_name = run_name
        self.interval = interval
        self.max_units = max_units
        self.sleep = sleep
        self.clock = clock
        self.on_progress = on_progress
        self.breaker = Breaker()
        self.run_id: int | None = None
        self.store_ids: list[int] = []
        self.processed = 0

    # --- ciclo ---------------------------------------------------------------------

    async def run(self) -> str:
        """Procesa hasta terminar (o hasta max_units). Devuelve el estado final de la corrida."""
        self.store_ids = await load_store_ids()
        await self._start()
        last_heartbeat = self.clock()
        await self.heartbeat("inicio")
        status = "terminada"
        try:
            while True:
                if self.max_units is not None and self.processed >= self.max_units:
                    status = "detenida"
                    break
                task = await self._next_task()
                if task is None:
                    break
                started = self.clock()
                outcome = await self.process(task)
                self.processed += 1
                if self.on_progress:
                    self.on_progress(task, outcome)
                if self.breaker.record(is_gateway_error(outcome.error_type)):
                    await self.heartbeat(f"freno: {self.breaker.threshold} errores de pasarela seguidos, "
                                         f"pausa de {BREAKER_PAUSE} s")
                    await self.sleep(BREAKER_PAUSE)
                if self.clock() - last_heartbeat >= HEARTBEAT_SECONDS:
                    await self.heartbeat()
                    last_heartbeat = self.clock()
                wait = self.interval - (self.clock() - started)
                if wait > 0:
                    await self.sleep(wait)
        except (asyncio.CancelledError, KeyboardInterrupt):
            status = "interrumpida"
            raise
        except Exception:
            # Algo que los reintentos no cubren (por ejemplo, se cayó la base): queda en la bitácora.
            status = "caida"
            logger.exception("La corrida se detuvo por un error")
            raise
        finally:
            await self._finish(status)
        return status

    async def _start(self) -> None:
        async with SessionLocal() as session, session.begin():
            run = await session.scalar(select(LoadRun).where(LoadRun.name == self.run_name).with_for_update())
            if run is None:
                raise ValueError(f"No existe la corrida «{self.run_name}».")
            self.run_id = run.id
            run.status = "corriendo"
            run.started_at = run.started_at or datetime.now(UTC)
            run.finished_at = None
            # Unidades que quedaron a medias por una caída: vuelven a la cola (su intento ya contó).
            recovered = await session.execute(
                update(LoadTask).where(LoadTask.run_id == run.id, LoadTask.status == RUNNING).values(status=PENDING)
            )
            if recovered.rowcount:
                logger.warning("Se recuperaron %s unidades que quedaron a medias", recovered.rowcount)

    async def _finish(self, status: str) -> None:
        try:
            await self.heartbeat(f"fin: {status}")
            async with SessionLocal() as session, session.begin():
                run = await session.get(LoadRun, self.run_id)
                run.status = status
                run.finished_at = datetime.now(UTC)
                if status != "terminada":
                    await session.execute(
                        update(LoadTask)
                        .where(LoadTask.run_id == self.run_id, LoadTask.status == RUNNING)
                        .values(status=PENDING)
                    )
        except Exception:
            logger.exception("No se pudo cerrar la corrida")

    async def _next_task(self) -> LoadTask | None:
        async with SessionLocal() as session:
            return await session.scalar(
                select(LoadTask)
                .where(LoadTask.run_id == self.run_id, LoadTask.status == PENDING)
                .order_by(LoadTask.position)
                .limit(1)
            )

    # --- una unidad ----------------------------------------------------------------

    async def process(self, task: LoadTask) -> Outcome:
        usage_task_id = f"r{self.run_id}-{task.unit_id}"[:32]
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        with track_task(usage_task_id, store_id=self.store_ids[task.store_index]) as usage:
            outcome = await self._attempts(task)
        latency_ms = int((time.perf_counter() - started) * 1000)
        usage.outcome = outcome.status
        await self._save(task, outcome, usage, started_at, latency_ms)
        await save_usage(usage)
        return outcome

    async def _attempts(self, task: LoadTask) -> Outcome:
        for attempt in range(MAX_ATTEMPTS):
            await self._mark_running(task)
            try:
                return await asyncio.wait_for(self._attempt(task), UNIT_TIMEOUT)
            except Exception as exc:
                error_type, transient = classify_error(exc)
                if not transient or attempt == MAX_ATTEMPTS - 1:
                    return Outcome(ERROR, error_type=error_type, error_message=str(exc)[:500])
                logger.warning("Unidad %s: %s, reintento %s", task.unit_id, error_type, attempt + 2)
                await self.sleep(RETRY_DELAYS[attempt])
        raise AssertionError("inalcanzable")

    async def _mark_running(self, task: LoadTask) -> None:
        async with SessionLocal() as session, session.begin():
            await session.execute(
                update(LoadTask)
                .where(LoadTask.id == task.id)
                .values(status=RUNNING, attempts=LoadTask.attempts + 1, started_at=func.coalesce(LoadTask.started_at, func.now()))
            )

    async def _attempt(self, task: LoadTask) -> Outcome:
        store_id = self.store_ids[task.store_index]
        async with SessionLocal() as session:
            products = await handlers.load_active_products(session, (await session.get(Store, store_id)).distributor_id)
            stock = {}
            if "stock_de" in task.expected:
                analysis = await load_store_analysis(session, store_id)
                stock = {item.sku: item.stock for item in analysis.products}
        expected = resolve_expected(task.expected, stock)
        observed = Observed()
        actions = recording_actions(handlers.store_actions(store_id, products, "text", {}), observed)
        result = await run_agent(task.text, build_tools(actions, user_message=task.text), AGENT_SYSTEM_PROMPT)
        observed.tools = [call.name for call in result.tool_calls]
        observed.reply = result.reply
        observed.finished = result.finished
        failures = evaluate(expected, observed)
        return Outcome(FAILED if failures else SUCCESS, observed, failures)

    async def _save(self, task: LoadTask, outcome: Outcome, usage: TaskUsage, started_at: datetime,
                    latency_ms: int) -> None:
        """Resultado y confirmación de la venta en una sola transacción: nunca una venta dos veces."""
        store_id = self.store_ids[task.store_index]
        observed = outcome.observed
        async with SessionLocal() as session, session.begin():
            row = await session.get(LoadTask, task.id, with_for_update=True)
            confirmed = False
            if outcome.status == SUCCESS and row.confirm and observed.sales:
                store = await session.get(Store, store_id)
                skus = [sku for sku, _ in observed.sales]
                products = {
                    p.sku: p
                    for p in (await session.scalars(
                        select(Product).where(Product.distributor_id == store.distributor_id, Product.sku.in_(skus))
                    )).all()
                }
                now = datetime.now(UTC)
                for sku, quantity in observed.sales:
                    product = products[sku]
                    session.add(Sale(store_id=store_id, product_id=product.id, quantity=quantity,
                                     unit_price=product.unit_price, sold_at=now, source="text"))
                confirmed = True
            if row.kind in SALE_KINDS or observed.sales:
                # El tendero confirma o cancela el resumen: no queda un borrador de ventas abierto.
                await session.execute(
                    delete(PendingAction).where(PendingAction.store_id == store_id, PendingAction.kind == handlers.KIND_SALES)
                )
            row.status = outcome.status
            row.started_at = row.started_at or started_at
            row.finished_at = datetime.now(UTC)
            row.latency_ms = latency_ms
            row.tools = observed.tools
            row.observed = {"ventas": observed.sales, "cambios": observed.changes, "termino": observed.finished}
            row.reply = observed.reply or None
            row.failures = outcome.failures or None
            row.confirmed = confirmed
            row.error_type = outcome.error_type
            row.error_message = outcome.error_message
            for key, value in _usage_fields(usage).items():
                setattr(row, key, value)

    # --- señal de vida -------------------------------------------------------------

    async def heartbeat(self, note: str | None = None) -> None:
        try:
            async with SessionLocal() as session, session.begin():
                counts = dict(
                    (await session.execute(
                        select(LoadTask.status, func.count()).where(LoadTask.run_id == self.run_id).group_by(LoadTask.status)
                    )).all()
                )
                cost = await session.scalar(
                    select(func.coalesce(func.sum(LoadTask.cost_ticks), 0)).where(LoadTask.run_id == self.run_id)
                )
                done = sum(counts.get(state, 0) for state in (SUCCESS, FAILED, ERROR))
                session.add(LoadHeartbeat(
                    run_id=self.run_id, done=done, pending=counts.get(PENDING, 0) + counts.get(RUNNING, 0),
                    success=counts.get(SUCCESS, 0), failed=counts.get(FAILED, 0), errors=counts.get(ERROR, 0),
                    cost_ticks=int(cost), note=note,
                ))
        except Exception:
            # La señal de vida nunca detiene la corrida; si falla, el hueco se verá en la bitácora.
            logger.exception("No se pudo guardar la señal de vida")
