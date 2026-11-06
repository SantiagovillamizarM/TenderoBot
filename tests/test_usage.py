"""Bitácora de consumo: costo por llamada y por tarea."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import usage_log
from app.ai import grok_client
from app.ai.agent import AgentResult, ToolCallRecord
from app.ai.usage import (
    TICKS_PER_USD,
    LlmCall,
    call_from_response,
    current_task,
    estimate_ticks,
    record,
    track_task,
)
from app.bot import handlers

MODEL = "grok-4.20-0309-non-reasoning"

# Respuesta real de la pasarela (noviembre 2026), solo el bloque usage.
GATEWAY_BODY = {
    "usage": {
        "prompt_tokens": 187,
        "completion_tokens": 5,
        "prompt_tokens_details": {"cached_tokens": 128},
        "cost_in_usd_ticks": 1118500,
    }
}


def test_cost_comes_from_gateway():
    call = call_from_response("chat", MODEL, GATEWAY_BODY, latency_ms=380)
    assert (call.prompt_tokens, call.cached_tokens, call.completion_tokens) == (187, 128, 5)
    assert call.cost_ticks == 1118500
    assert not call.cost_estimated
    assert call.cost_usd == pytest.approx(0.00011185)


def test_estimate_matches_gateway_price_table():
    # 59 sin caché + 128 en caché + 5 de salida = lo mismo que cobró la pasarela.
    assert estimate_ticks(MODEL, 187, 128, 5) == 1118500
    assert estimate_ticks("modelo-desconocido", 10, 0, 1) is None


def test_missing_cost_is_estimated_and_flagged():
    body = {"usage": {"prompt_tokens": 1000, "completion_tokens": 100}}
    call = call_from_response("chat", MODEL, body, latency_ms=1)
    assert call.cost_ticks == 1000 * 12_500 + 100 * 25_000
    assert call.cost_estimated


def test_stt_without_usage_has_unknown_cost():
    call = call_from_response("stt", "stt", {"text": "hola"}, latency_ms=900)
    assert call.cost_ticks is None
    assert call.cost_usd == 0


def test_failed_call_is_recorded_with_zero_tokens():
    call = call_from_response("chat", MODEL, None, latency_ms=60000, ok=False)
    assert not call.ok and call.prompt_tokens == 0 and call.cost_ticks is None


def test_task_sums_all_calls_and_flags_incomplete_cost():
    with track_task("voz") as usage:
        record(LlmCall("stt", "stt", latency_ms=800))
        record(LlmCall("chat", MODEL, 1000, 900, 50, cost_ticks=5_000_000, latency_ms=400))
        record(LlmCall("chat", MODEL, 1200, 1000, 30, cost_ticks=4_000_000, latency_ms=300))
    assert current_task() is None
    assert usage.prompt_tokens == 2200 and usage.completion_tokens == 80
    assert usage.cost_ticks == 9_000_000
    assert usage.cost_usd == 9_000_000 / TICKS_PER_USD
    assert usage.latency_ms == 1500
    assert not usage.cost_complete


def test_nested_task_reuses_outer_one():
    with track_task("voz") as outer:
        with track_task("texto") as inner:
            record(LlmCall("chat", MODEL))
    assert inner is outer and len(outer.calls) == 1


def test_calls_outside_a_task_are_ignored():
    record(LlmCall("chat", MODEL))
    assert current_task() is None


# --- Cliente de Grok -----------------------------------------------------------


def fake_client(monkeypatch, handler):
    client = httpx.AsyncClient(base_url="https://pasarela.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(grok_client, "_client", client)
    monkeypatch.setattr(grok_client, "get_settings", lambda: SimpleNamespace(grok_model=MODEL))


@pytest.mark.asyncio
async def test_chat_with_tools_records_the_call(monkeypatch):
    def handler(request):
        message = {"role": "assistant", "content": "hola"}
        return httpx.Response(200, json={"choices": [{"message": message}], **GATEWAY_BODY})

    fake_client(monkeypatch, handler)
    with track_task("texto") as usage:
        message, raw = await grok_client.chat_with_tools([{"role": "user", "content": "hola"}], [])
    assert message["content"] == "hola"
    assert raw["cost_in_usd_ticks"] == 1118500
    assert [(c.endpoint, c.cost_ticks, c.ok) for c in usage.calls] == [("chat", 1118500, True)]


@pytest.mark.asyncio
async def test_failed_http_call_is_still_recorded(monkeypatch):
    fake_client(monkeypatch, lambda request: httpx.Response(503, json={"error": "caída"}))
    with track_task("texto") as usage:
        with pytest.raises(httpx.HTTPStatusError):
            await grok_client.chat_with_tools([], [])
    assert len(usage.calls) == 1 and not usage.calls[0].ok


@pytest.mark.asyncio
async def test_transcription_is_recorded_as_stt(monkeypatch):
    fake_client(monkeypatch, lambda request: httpx.Response(200, json={"text": "vendí dos cocas"}))
    with track_task("voz") as usage:
        assert await grok_client.transcribe_audio(b"ogg", "audio/ogg") == "vendí dos cocas"
    assert usage.calls[0].endpoint == "stt"


# --- Guardar en la base y resultado de la tarea -------------------------------


def test_usage_rows_keep_task_and_store():
    with track_task("texto", store_id=7) as usage:
        usage.outcome = "ventas"
        record(LlmCall("chat", MODEL, 100, 0, 5, cost_ticks=10, latency_ms=3))
    rows = usage_log.usage_rows(usage)
    assert len(rows) == 1
    row = rows[0]
    assert (row.task, row.store_id, row.outcome, row.cost_ticks) == ("texto", 7, "ventas", 10)
    assert row.task_id == usage.task_id


@pytest.mark.asyncio
async def test_logged_task_saves_once_even_on_error(monkeypatch):
    saved = AsyncMock()
    monkeypatch.setattr(usage_log, "save_usage", saved)
    with pytest.raises(RuntimeError):
        async with usage_log.logged_task("texto"):
            async with usage_log.logged_task("voz"):
                usage_log.set_task_info(store_id=3, outcome="ventas")
                record(LlmCall("chat", MODEL))
            raise RuntimeError("falló el handler")
    saved.assert_awaited_once()
    usage = saved.call_args.args[0]
    assert (usage.task, usage.store_id, usage.outcome, len(usage.calls)) == ("texto", 3, "ventas", 1)


@pytest.mark.asyncio
async def test_save_usage_never_raises(monkeypatch):
    def broken():
        raise RuntimeError("sin base")

    monkeypatch.setattr(usage_log, "SessionLocal", broken)
    with track_task("texto") as usage:
        record(LlmCall("chat", MODEL))
    await usage_log.save_usage(usage)


def test_agent_outcome_labels():
    sales = {handlers.KIND_SALES: handlers.BotMessage("x")}
    order = {handlers.KIND_ORDER: handlers.BotMessage("x")}
    asked = AgentResult(reply="7", tool_calls=[ToolCallRecord("consultar_stock", {}, {}, True)])
    assert handlers.agent_outcome(AgentResult(reply="x"), sales) == "ventas"
    assert handlers.agent_outcome(AgentResult(reply="x"), order) == "pedido"
    assert handlers.agent_outcome(asked, {}) == "consulta"
    assert handlers.agent_outcome(AgentResult(reply="hola"), {}) == "conversacion"
    assert handlers.agent_outcome(AgentResult(reply="", finished=False), {}) == "sin_terminar"
    assert handlers.agent_outcome(None, {}) == "error"
