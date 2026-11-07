"""El agente conectado al bot: qué se le muestra al tendero y cuándo se usa el respaldo."""

import json
import math
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.ai.agent import AgentResult
from app.bot import handlers, texts
from app.inventory.engine import COLOMBIA_TZ as COLOMBIA
from app.schemas import OrderChange, SaleLine


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

    store = SimpleNamespace(id=7, distributor_id=1)

    async def get(model, pk):
        return store if pk == store.id else None

    @asynccontextmanager
    async def fake_scope():
        yield SimpleNamespace(get=get)

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
        await tools["registrar_ventas"].run({"ventas": [{"producto": "coca litro", "cantidad": 2, "texto_cantidad": "2"}]})
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
        await tools["registrar_ventas"].run({"ventas": [{"producto": "coca", "cantidad": 1, "texto_cantidad": "1"}]})
        await tools["registrar_ventas"].run({"ventas": [{"producto": "coca", "cantidad": 3, "texto_cantidad": "3"}]})
        return ""

    fake_agent(monkeypatch, behaviour)
    await handlers.answer_with_agent(None, 7, 1, "vendí 1 coca, no, mejor 3 coca", "text", [COCA])
    assert len(bot.replies) == 1
    assert "• 3 Coca-Cola 1 litro" in bot.replies[0][0]


@pytest.mark.asyncio
async def test_unrecognized_sales_do_not_create_draft(bot, monkeypatch):
    seen = {}

    async def behaviour(tools):
        seen["result"] = await tools["registrar_ventas"].run({"ventas": [{"producto": "tornillos", "cantidad": 1, "texto_cantidad": "1"}]})
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
        await by_name["registrar_ventas"].run({"ventas": [{"producto": "coca", "cantidad": 2, "texto_cantidad": "2"}]})
        raise RuntimeError("se cayó en la segunda vuelta")

    monkeypatch.setattr(handlers, "run_agent", half)
    assert await handlers.answer_with_agent(None, 7, 1, "vendí 2 coca litro", "text", [COCA]) is True
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
    monkeypatch.setattr(handlers, "store_products", products)
    monkeypatch.setattr(handlers, "get_pending", no_pending)
    monkeypatch.setattr(handlers, "answer_with_agent", agent)
    monkeypatch.setattr(handlers, "extract_sales", extract)
    monkeypatch.setattr(handlers, "get_settings", lambda: SimpleNamespace(agent_enabled=enabled))
    await handlers.route_text(None, "vendí 2 cocas", "text")
    assert calls == (["agent"] if enabled else ["extract"])


# --- Lo que el agente lee y arma en la tienda ----------------------------------

DELIVERY = date(2026, 11, 12)
DEADLINE = datetime(2026, 11, 11, 18, 0, tzinfo=COLOMBIA)


def fake_analysis():
    coca = SimpleNamespace(
        sku="GAS-COCA-1L", name="Coca-Cola 1 litro", stock=7, has_count=True, coverage_days=1.75,
        reason="Se acaba en 2 días",
    )
    pan = SimpleNamespace(
        sku="PAN-TAJA-UND", name="Pan tajado", stock=0, has_count=False, coverage_days=math.inf,
        reason="No se ha vendido en 4 semanas",
    )
    return SimpleNamespace(
        products=[coca, pan],
        at_risk=[coca],
        stagnant=[pan],
        next_delivery=DELIVERY,
        order_deadline=DEADLINE,
        order_lines=[SimpleNamespace(product_id=10, cases=2, reason="Se acaba en 2 días")],
    )


@pytest.fixture
def store_data(monkeypatch):
    async def analysis(session, store_id):
        return fake_analysis()

    async def products(session, distributor_id):
        return [COCA, PAN]

    async def not_sent(session, store_id, delivery):
        return False

    monkeypatch.setattr(handlers, "load_store_analysis", analysis)
    monkeypatch.setattr(handlers, "load_active_products", products)
    monkeypatch.setattr(handlers, "order_sent_for", not_sent)


def actions(shown=None):
    return handlers.store_actions(7, [COCA, PAN], "text", shown if shown is not None else {})


@pytest.mark.asyncio
async def test_stock_tool_reads_engine_without_infinite_days(bot, store_data):
    rows = await actions().stock()
    assert rows[0] == {
        "sku": "GAS-COCA-1L",
        "producto": "Coca-Cola 1 litro",
        "unidades": 7,
        "tiene_conteo": True,
        "dias_que_alcanza": 1.8,
    }
    # Sin ventas la cobertura es infinita: al modelo le llega null, no "inf".
    assert rows[1]["dias_que_alcanza"] is None
    assert json.dumps(rows)


@pytest.mark.asyncio
async def test_alerts_tool_lists_risk_and_stagnant(bot, store_data):
    alerts = await actions().alerts()
    assert alerts["en_riesgo"] == [{"producto": "Coca-Cola 1 litro", "motivo": "Se acaba en 2 días"}]
    assert alerts["estancados"][0]["producto"] == "Pan tajado"
    assert alerts["proxima_entrega"] == "jueves 12/11"


@pytest.mark.asyncio
async def test_suggest_order_creates_draft_and_shows_it(bot, store_data):
    shown = {}
    info = await actions(shown).suggest_order()
    assert info["borrador"] == "creado"
    assert info["total_pesos"] == 2 * 12 * 4500
    assert info["entrega"] == "jueves 12/11"
    assert bot.drafts[0].kind == handlers.KIND_ORDER
    assert bot.drafts[0].payload["delivery_date"] == "2026-11-12"
    message = shown[handlers.KIND_ORDER]
    assert message.markup.inline_keyboard[0][0].callback_data.endswith(":1")


@pytest.mark.asyncio
async def test_suggest_order_when_already_sent(bot, store_data, monkeypatch):
    async def sent(session, store_id, delivery):
        return True

    monkeypatch.setattr(handlers, "order_sent_for", sent)
    shown = {}
    info = await actions(shown).suggest_order()
    assert info == {"ya_enviado": "2026-11-12"}
    assert bot.drafts == []
    assert shown[handlers.KIND_ORDER].text == texts.order_already_sent(DELIVERY)


@pytest.mark.asyncio
async def test_change_order_updates_open_draft(bot, monkeypatch):
    open_order = SimpleNamespace(
        id=40,
        kind="order",
        payload={
            "lines": [{"product_id": 10, "cases": 1, "reason": "Se acaba"}],
            "delivery_date": DELIVERY.isoformat(),
            "order_deadline": DEADLINE.isoformat(),
        },
    )

    async def pending(session, store_id, kind):
        return open_order

    monkeypatch.setattr(handlers, "get_pending", pending)
    shown = {}
    changes = [OrderChange(sku="GAS-COCA-1L", product_text="coca", action="add", quantity=1)]
    info = await actions(shown).change_order(changes)
    assert info["borrador"] == "actualizado"
    assert info["total_pesos"] == 2 * 12 * 4500
    assert bot.drafts[0].payload["lines"] == [{"product_id": 10, "cases": 2, "reason": "Se acaba"}]
    assert bot.drafts[0].payload["delivery_date"] == DELIVERY.isoformat()
    assert handlers.KIND_ORDER in shown


@pytest.mark.asyncio
async def test_pedido_command_shows_draft_with_buttons(bot, store_data, monkeypatch):
    async def ready(update, session):
        return SimpleNamespace(id=7, distributor_id=1)

    monkeypatch.setattr(handlers, "require_ready_store", ready)
    await handlers.handle_order(None, None)
    text, markup = bot.replies[0]
    assert "Coca-Cola 1 litro" in text
    assert markup is not None
    assert bot.drafts[0].kind == handlers.KIND_ORDER
