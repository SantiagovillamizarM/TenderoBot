"""Trabajador de carga: piezas sin base de datos (errores, ritmo, freno, registro, esperado)."""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from app.ai.tools import StoreActions
from app.evaluation import Observed
from app.load.runner import (
    Breaker,
    classify_error,
    is_gateway_error,
    pace_interval,
    recording_actions,
    resolve_expected,
)
from app.load.worker import LOAD_DISTRIBUTOR
from app.schemas import OrderChange, SaleLine
from scripts.seed import DISTRIBUTOR_NAME


def status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://pasarela.test/v1/chat/completions")
    return httpx.HTTPStatusError("error", request=request, response=httpx.Response(code, request=request))


@pytest.mark.parametrize(
    "exc, expected",
    [
        (status_error(429), ("pasarela_429", True)),
        (status_error(503), ("pasarela_503", True)),
        (status_error(400), ("pasarela_400", False)),
        (httpx.ReadTimeout("lento"), ("pasarela_tiempo", True)),
        (httpx.ConnectError("sin red"), ("red", True)),
        (asyncio.TimeoutError(), ("tiempo_unidad", True)),
        (KeyError("x"), ("codigo_KeyError", False)),
    ],
)
def test_classify_error(exc, expected):
    assert classify_error(exc) == expected


def test_gateway_errors_are_detected():
    assert is_gateway_error("pasarela_503") and is_gateway_error("red")
    assert not is_gateway_error("codigo_KeyError") and not is_gateway_error(None)


def test_pace_spreads_units_over_the_hours():
    assert pace_interval(1500, 12.5) == 30
    assert pace_interval(50, 0.5) == 36
    with pytest.raises(ValueError):
        pace_interval(0, 12)


def test_breaker_trips_after_consecutive_gateway_errors_only():
    breaker = Breaker(threshold=3)
    assert [breaker.record(True) for _ in range(2)] == [False, False]
    assert breaker.record(False) is False  # una unidad buena reinicia la cuenta
    assert [breaker.record(True) for _ in range(3)] == [False, False, True]
    assert breaker.trips == 1 and breaker.consecutive == 0


def actions():
    return StoreActions(
        catalog=[], stock=AsyncMock(), alerts=AsyncMock(),
        draft_sales=AsyncMock(return_value={"borrador": "creado"}),
        suggest_order=AsyncMock(),
        change_order=AsyncMock(side_effect=[{"error": "No hay un pedido abierto"}, {"borrador": "actualizado"}]),
    )


@pytest.mark.asyncio
async def test_recording_actions_keep_last_draft_and_ignore_failed_changes():
    observed = Observed()
    original = actions()
    wrapped = recording_actions(original, observed)
    await wrapped.draft_sales([SaleLine(sku="A", product_text="a", quantity=2)])
    await wrapped.draft_sales([])  # sin líneas reconocidas no cambia lo anotado
    assert observed.sales == [("A", 2)]
    change = [OrderChange(sku="B", action="add", quantity=1)]
    await wrapped.change_order(change)  # sin pedido abierto: no cuenta como cambio
    assert observed.changes == []
    await wrapped.change_order(change)
    assert observed.changes == [("B", "add", 1, "case")]
    assert original.draft_sales.await_count == 2


def test_resolve_expected_reads_current_stock():
    expected = {"herramientas": ["consultar_stock"], "stock_de": "A", "no_debe_llamar": ["registrar_ventas"]}
    resolved = resolve_expected(expected, {"A": 287})
    assert resolved == {"herramientas": ["consultar_stock"], "no_debe_llamar": ["registrar_ventas"],
                        "respuesta_contiene": ["287"]}
    assert "stock_de" in expected  # no modifica la unidad original
    assert resolve_expected({"ventas": []}, {}) == {"ventas": []}
    with pytest.raises(KeyError):
        resolve_expected({"stock_de": "Z"}, {"A": 1})


def test_worker_uses_the_seed_distributor():
    assert LOAD_DISTRIBUTOR == DISTRIBUTOR_NAME
