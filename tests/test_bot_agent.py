"""El agente conectado al bot: qué se le muestra al tendero y cuándo se usa el respaldo."""

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.ai.agent import AgentResult
from app.bot import handlers, texts
from app.schemas import SaleLine

COCA = SimpleNamespace(
    id=10,
    sku="GAS-COCA-1L",
    name="Coca-Cola 1 litro",
    aliases="coca litro",
    presentation="Botella 1 L",
    unit_price=Decimal("4500"),
    units_per_case=12,
    available_units=None,
)
PAN = SimpleNamespace(
    id=11,
    sku="PAN-TAJA-UND",
    name="Pan tajado",
    aliases="pan",
    presentation="Bolsa",
    unit_price=Decimal("6200"),
    units_per_case=10,
    available_units=3,
)


@pytest.fixture
def bot(monkeypatch):
    """Sesión falsa, respuestas grabadas y borradores en memoria."""
    replies: list[tuple[str, object]] = []
    drafts: list = []

    @asynccontextmanager
    async def fake_scope():
        yield SimpleNamespace()

    async def record(update, text, reply_markup=None):
        replies.append((text, reply_markup))

    async def replace(session, store_id, kind, payload, ttl=None):
        draft = SimpleNamespace(id=len(drafts) + 1, kind=kind, payload=payload)
        drafts.append(draft)
        return draft

    monkeypatch.setattr(handlers, "session_scope", fake_scope)
    monkeypatch.setattr(handlers, "_reply", record)
    monkeypatch.setattr(handlers, "replace_pending", replace)
    return SimpleNamespace(replies=replies, drafts=drafts)


def fake_agent(monkeypatch, behaviour):
    """Sustituye run_agent: `behaviour(tools)` hace lo que haría Grok y devuelve el texto final."""

    async def run(text, tools, system_prompt):
        by_name = {tool.name: tool for tool in tools}
        reply = await behaviour(by_name)
        return AgentResult(reply=reply)

    monkeypatch.setattr(handlers, "run_agent", run)


def test_agent_catalog_marks_sold_out_and_prices():
    catalog = handlers.agent_catalog([COCA, PAN])
    assert catalog[0]["unit_price"] == 4500 and catalog[0]["sold_out"] is False
    # 3 unidades no alcanzan para una caja de 10.
    assert catalog[1]["sold_out"] is True


@pytest.mark.asyncio
async def test_sales_reply_then_summary_with_buttons(bot, monkeypatch):
    async def behaviour(tools):
        await tools["registrar_ventas"].run({"ventas": [{"producto": "coca litro", "cantidad": 2}]})
        return "Revisa el resumen y confirma."

    fake_agent(monkeypatch, behaviour)
    handled = await handlers.answer_with_agent(None, 7, 1, "vendí 2 coca litro", "voice", [COCA, PAN])
    assert handled
    assert [text for text, _ in bot.replies][0] == "Revisa el resumen y confirma."
    summary, markup = bot.replies[1]
    assert summary.startswith("¿Confirmas estas ventas?") and "Coca-Cola 1 litro" in summary
    assert markup.inline_keyboard[0][0].callback_data == "sales:confirm:1"
    assert bot.drafts[0].payload == {
        "source": "voice",
        "lines": [SaleLine(sku="GAS-COCA-1L", product_text="coca litro", quantity=2).model_dump()],
    }


@pytest.mark.asyncio
async def test_only_last_draft_of_each_kind_is_shown(bot, monkeypatch):
    async def behaviour(tools):
        await tools["registrar_ventas"].run({"ventas": [{"producto": "coca", "cantidad": 1}]})
        await tools["registrar_ventas"].run({"ventas": [{"producto": "coca", "cantidad": 3}]})
        return ""

    fake_agent(monkeypatch, behaviour)
    await handlers.answer_with_agent(None, 7, 1, "x", "text", [COCA])
    assert len(bot.replies) == 1
    assert "• 3 Coca-Cola 1 litro" in bot.replies[0][0]


@pytest.mark.asyncio
async def test_unrecognized_sales_do_not_create_draft(bot, monkeypatch):
    seen = {}

    async def behaviour(tools):
        seen["result"] = await tools["registrar_ventas"].run({"ventas": [{"producto": "tornillos", "cantidad": 1}]})
        return "No encontré tornillos en tu catálogo."

    fake_agent(monkeypatch, behaviour)
    await handlers.answer_with_agent(None, 7, 1, "vendí un tornillo", "text", [COCA])
    assert bot.drafts == []
    assert seen["result"]["borrador"] == "no_creado"
    assert bot.replies == [("No encontré tornillos en tu catálogo.", None)]


@pytest.mark.asyncio
async def test_change_order_without_open_order_tells_the_model(bot, monkeypatch):
    async def no_pending(session, store_id, kind):
        return None

    monkeypatch.setattr(handlers, "get_pending", no_pending)
    seen = {}

    async def behaviour(tools):
        seen["result"] = await tools["ajustar_pedido"].run(
            {"cambios": [{"producto": "coca", "accion": "add", "cantidad": 1, "unidad": "case"}]}
        )
        return "Primero arma el pedido con /pedido."

    fake_agent(monkeypatch, behaviour)
    await handlers.answer_with_agent(None, 7, 1, "ponle una caja de coca", "text", [COCA])
    assert "error" in seen["result"]
    assert bot.replies == [("Primero arma el pedido con /pedido.", None)]


@pytest.mark.asyncio
async def test_agent_failure_before_any_action_uses_fallback(bot, monkeypatch):
    async def broken(text, tools, system_prompt):
        raise RuntimeError("pasarela caída")

    monkeypatch.setattr(handlers, "run_agent", broken)
    assert await handlers.answer_with_agent(None, 7, 1, "x", "text", [COCA]) is False
    assert bot.replies == []


@pytest.mark.asyncio
async def test_agent_failure_after_draft_still_shows_it(bot, monkeypatch):
    async def half(text, tools, system_prompt):
        by_name = {tool.name: tool for tool in tools}
        await by_name["registrar_ventas"].run({"ventas": [{"producto": "coca", "cantidad": 2}]})
        raise RuntimeError("se cayó en la segunda vuelta")

    monkeypatch.setattr(handlers, "run_agent", half)
    assert await handlers.answer_with_agent(None, 7, 1, "x", "text", [COCA]) is True
    assert len(bot.replies) == 1 and bot.replies[0][0].startswith("¿Confirmas estas ventas?")


@pytest.mark.asyncio
async def test_empty_agent_answer_says_it_did_not_understand(bot, monkeypatch):
    async def nothing(tools):
        return ""

    fake_agent(monkeypatch, nothing)
    await handlers.answer_with_agent(None, 7, 1, "x", "text", [COCA])
    assert bot.replies == [(texts.AI_FAIL, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_route_text_respects_agent_flag(bot, monkeypatch, enabled):
    store = SimpleNamespace(id=7, distributor_id=1)
    calls = []

    async def ready(update, session):
        return store

    async def products(session, distributor_id):
        return [COCA]

    async def no_pending(session, store_id, kind):
        return None

    async def agent(update, store_id, distributor_id, text, source, products):
        calls.append("agent")
        return True

    async def extract(catalog, transcript=None):
        calls.append("extract")
        return []

    monkeypatch.setattr(handlers, "require_ready_store", ready)
    monkeypatch.setattr(handlers, "load_active_products", products)
    monkeypatch.setattr(handlers, "get_pending", no_pending)
    monkeypatch.setattr(handlers, "answer_with_agent", agent)
    monkeypatch.setattr(handlers, "extract_sales", extract)
    monkeypatch.setattr(handlers, "get_settings", lambda: SimpleNamespace(agent_enabled=enabled))
    await handlers.route_text(None, "vendí 2 cocas", "text")
    assert calls == (["agent"] if enabled else ["extract"])
