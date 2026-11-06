"""Bitácora de consumo de Grok: tokens, costo y latencia de cada llamada, agrupadas por tarea.

Una tarea es lo que pide el tendero en un mensaje (un texto, un audio o una foto). Puede costar
varias llamadas: la transcripción del audio, las vueltas del agente o un reintento. El costo por
tarea suma todas.

El costo sale de la pasarela del reto: cada respuesta trae `usage.cost_in_usd_ticks`
(1 USD = 10^10 ticks). Si no lo trae, se calcula con la tabla de precios de /models.

Este módulo no toca la base de datos: el bot guarda las tareas (app/usage_log.py) y el runner de la
evaluación las escribe en sus resultados.
"""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

TICKS_PER_USD = 10_000_000_000

# Ticks por token, de GET /models de la pasarela (noviembre 2026). Solo como respaldo.
PRICES = {
    "grok-4.20-0309-non-reasoning": {"prompt": 12_500, "cached": 2_000, "completion": 25_000},
    "grok-4.20-0309-reasoning": {"prompt": 12_500, "cached": 2_000, "completion": 25_000},
}


@dataclass
class LlmCall:
    endpoint: str  # "chat" o "stt"
    model: str
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    cost_ticks: int | None = None
    latency_ms: int = 0
    ok: bool = True
    # True si el costo lo calculamos nosotros (la respuesta no lo traía).
    cost_estimated: bool = False

    @property
    def cost_usd(self) -> float:
        return (self.cost_ticks or 0) / TICKS_PER_USD


@dataclass
class TaskUsage:
    task: str  # de dónde vino: "texto", "voz", "foto" o el caso de la evaluación
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    store_id: int | None = None
    # Qué terminó haciendo: "ventas", "pedido", "consulta", "respaldo"... (lo pone el bot).
    outcome: str | None = None
    calls: list[LlmCall] = field(default_factory=list)

    @property
    def prompt_tokens(self) -> int:
        return sum(call.prompt_tokens for call in self.calls)

    @property
    def cached_tokens(self) -> int:
        return sum(call.cached_tokens for call in self.calls)

    @property
    def completion_tokens(self) -> int:
        return sum(call.completion_tokens for call in self.calls)

    @property
    def cost_ticks(self) -> int:
        return sum(call.cost_ticks or 0 for call in self.calls)

    @property
    def cost_usd(self) -> float:
        return self.cost_ticks / TICKS_PER_USD

    @property
    def latency_ms(self) -> int:
        return sum(call.latency_ms for call in self.calls)

    @property
    def cost_complete(self) -> bool:
        """False si alguna llamada (p. ej. la transcripción) no reportó costo."""
        return all(call.cost_ticks is not None for call in self.calls)


_current: ContextVar[TaskUsage | None] = ContextVar("tenderobot_task_usage", default=None)


@contextmanager
def track_task(task: str, store_id: int | None = None) -> Iterator[TaskUsage]:
    """Junta todas las llamadas a Grok hechas dentro del bloque. Si ya hay una tarea abierta, usa esa."""
    current = _current.get()
    if current is not None:
        yield current
        return
    usage = TaskUsage(task=task, store_id=store_id)
    token = _current.set(usage)
    try:
        yield usage
    finally:
        _current.reset(token)


def current_task() -> TaskUsage | None:
    return _current.get()


def estimate_ticks(model: str, prompt_tokens: int, cached_tokens: int, completion_tokens: int) -> int | None:
    prices = PRICES.get(model)
    if prices is None:
        return None
    uncached = max(0, prompt_tokens - cached_tokens)
    return uncached * prices["prompt"] + cached_tokens * prices["cached"] + completion_tokens * prices["completion"]


def call_from_response(endpoint: str, model: str, body: dict | None, latency_ms: int, ok: bool = True) -> LlmCall:
    usage = (body or {}).get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    ticks = usage.get("cost_in_usd_ticks")
    estimated = False
    if ticks is None and (prompt or completion):
        ticks = estimate_ticks(model, prompt, cached, completion)
        estimated = ticks is not None
    return LlmCall(
        endpoint=endpoint,
        model=model,
        prompt_tokens=prompt,
        cached_tokens=cached,
        completion_tokens=completion,
        cost_ticks=int(ticks) if ticks is not None else None,
        latency_ms=latency_ms,
        ok=ok,
        cost_estimated=estimated,
    )


def record(call: LlmCall) -> None:
    usage = _current.get()
    if usage is not None:
        usage.calls.append(call)
