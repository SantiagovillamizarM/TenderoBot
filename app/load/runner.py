"""Piezas del trabajador de carga que no tocan la base (se prueban solas).

- classify_error: de qué tipo es un error y si vale la pena reintentar.
- pace_interval: cada cuántos segundos procesar una unidad para cubrir las horas pedidas.
- Breaker: si la pasarela falla muchas veces seguidas, pausa en vez de gastar intentos.
- recording_actions: envuelve las herramientas del bot para anotar qué ventas o cambios armó el agente.
- resolve_expected: completa lo esperado que depende de la base (el stock actual).
"""

import asyncio
from dataclasses import dataclass, replace

import httpx

from app.ai.tools import StoreActions
from app.evaluation import Observed
from app.schemas import OrderChange, SaleLine

MAX_ATTEMPTS = 3
RETRY_DELAYS = (30, 90)  # espera antes del 2.º y del 3.er intento (segundos)
UNIT_TIMEOUT = 150  # segundos máximos por intento
BREAKER_THRESHOLD = 5  # unidades seguidas con error de pasarela
BREAKER_PAUSE = 300  # segundos de pausa cuando se activa el freno


def classify_error(exc: BaseException) -> tuple[str, bool]:
    """(tipo, se puede reintentar). Los tipos van a la bitácora."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return "pasarela_429", True
        if code >= 500:
            return f"pasarela_{code}", True
        return f"pasarela_{code}", False
    if isinstance(exc, httpx.TimeoutException):
        return "pasarela_tiempo", True
    if isinstance(exc, httpx.TransportError):
        return "red", True
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "tiempo_unidad", True
    return f"codigo_{type(exc).__name__}"[:40], False


def pace_interval(total_units: int, hours: float) -> float:
    """Segundos entre el inicio de una unidad y la siguiente para repartir todas en `hours`."""
    if total_units <= 0 or hours <= 0:
        raise ValueError("se necesitan unidades y horas positivas")
    return hours * 3600 / total_units


@dataclass
class Breaker:
    threshold: int = BREAKER_THRESHOLD
    consecutive: int = 0
    trips: int = 0

    def record(self, gateway_error: bool) -> bool:
        """Anota una unidad. True si hay que pausar (muchos errores de pasarela seguidos)."""
        if not gateway_error:
            self.consecutive = 0
            return False
        self.consecutive += 1
        if self.consecutive >= self.threshold:
            self.consecutive = 0
            self.trips += 1
            return True
        return False


def is_gateway_error(error_type: str | None) -> bool:
    return bool(error_type) and error_type.startswith(("pasarela_", "red"))


def recording_actions(actions: StoreActions, observed: Observed) -> StoreActions:
    """Las mismas herramientas, pero anotando en `observed` el último borrador de ventas o de pedido."""

    async def draft_sales(lines: list[SaleLine]) -> dict:
        result = await actions.draft_sales(lines)
        if lines:
            observed.sales = [(str(line.sku), line.quantity) for line in lines]
        return result

    async def change_order(changes: list[OrderChange]) -> dict:
        result = await actions.change_order(changes)
        if "error" not in result:
            observed.changes = [(str(c.sku), c.action, c.quantity, c.unit) for c in changes]
        return result

    return replace(actions, draft_sales=draft_sales, change_order=change_order)


def resolve_expected(expected: dict, stock_by_sku: dict[str, int]) -> dict:
    """`stock_de: sku` -> la respuesta debe decir el stock que tiene la tienda en este momento."""
    resolved = {key: value for key, value in expected.items() if key != "stock_de"}
    sku = expected.get("stock_de")
    if sku is not None:
        if sku not in stock_by_sku:
            raise KeyError(f"la tienda no tiene el producto {sku}")
        resolved["respuesta_contiene"] = list(resolved.get("respuesta_contiene", [])) + [str(stock_by_sku[sku])]
    return resolved
