"""Guarda en la base la bitácora de consumo de cada tarea (ver app/ai/usage.py)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.ai.usage import TaskUsage, current_task, track_task
from app.db import SessionLocal
from app.models import LlmUsage

logger = logging.getLogger(__name__)


def usage_rows(usage: TaskUsage) -> list[LlmUsage]:
    return [
        LlmUsage(
            task_id=usage.task_id,
            task=usage.task,
            outcome=usage.outcome,
            store_id=usage.store_id,
            endpoint=call.endpoint,
            model=call.model,
            prompt_tokens=call.prompt_tokens,
            cached_tokens=call.cached_tokens,
            completion_tokens=call.completion_tokens,
            cost_ticks=call.cost_ticks,
            cost_estimated=call.cost_estimated,
            latency_ms=call.latency_ms,
            ok=call.ok,
        )
        for call in usage.calls
    ]


async def save_usage(usage: TaskUsage) -> None:
    """Nunca rompe la respuesta al tendero: si falla, solo queda un aviso en el log."""
    if not usage.calls:
        return
    try:
        async with SessionLocal() as session:
            session.add_all(usage_rows(usage))
            await session.commit()
    except Exception:
        logger.warning("No se pudo guardar la bitácora de consumo")


@asynccontextmanager
async def logged_task(task: str) -> AsyncIterator[TaskUsage]:
    """Junta las llamadas a Grok del bloque y al final las guarda (también si hubo error).

    Si ya hay una tarea abierta, el bloque es parte de ella: la guarda quien la abrió.
    """
    outer = current_task()
    if outer is not None:
        yield outer
        return
    with track_task(task) as usage:
        try:
            yield usage
        finally:
            await save_usage(usage)


def set_task_info(store_id: int | None = None, outcome: str | None = None) -> None:
    usage = current_task()
    if usage is None:
        return
    if store_id is not None:
        usage.store_id = store_id
    if outcome is not None:
        usage.outcome = outcome
