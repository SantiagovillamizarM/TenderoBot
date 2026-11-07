"""Dejar de trabajar con un proveedor (o cancelar la solicitud) desde /proveedores."""

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app import suppliers
from app.bot import handlers, keyboards, provider_menu, texts
from app.catalog_merge import Offer, group_offers, match_item
from app.models import StoreSupplier
from tests.test_provider_menu import Query, option, press, with_options


def product(id, name, distributor_id, presentation="", price="4500"):
    return SimpleNamespace(id=id, sku=f"S{id}", name=name, presentation=presentation, unit_price=Decimal(price),
                           units_per_case=12, available_units=None, distributor_id=distributor_id, aliases="")


# --- El inventario no se pierde -------------------------------------------------------


def test_product_of_a_former_supplier_matches_todays_item():
    items = group_offers([
        Offer(product(1, "Coca-Cola 1 litro", 20, "1 L"), 20, "Postobón"),
        Offer(product(2, "Arroz Diana 500 g", 20, "500 g"), 20, "Postobón"),
    ])
    former = product(9, "Coca Cola 1 Litro", 10, "1 L")  # de El Barrio, que la tienda dejó
    assert match_item(items, former).preferred.product.id == 1
    assert match_item(items, product(8, "Coca-Cola 400 ml", 10, "400 ml")) is None  # otra presentación


# --- end_supplier ------------------------------------------------------------------------


class Session:
    def __init__(self, link):
        self.link = link
        self.deleted, self.executed = [], []

    async def scalar(self, statement):
        return self.link

    async def delete(self, row):
        self.deleted.append(row)

    async def execute(self, statement):
        self.executed.append(str(statement))

    async def flush(self):
        return None


def store(**overrides):
    base = dict(id=7, distributor_id=10, route_weekdays="0,2", visit_weekdays="0", lead_time_days=1,
                delivery_days_set_at=datetime(2026, 11, 1, tzinfo=UTC), last_reminder_for=None)
    return SimpleNamespace(**{**base, **overrides})


@pytest.mark.asyncio
async def test_leaving_the_main_supplier_passes_it_to_the_next(monkeypatch):
    following = StoreSupplier(store_id=7, distributor_id=20, status="approved", route_weekdays="5",
                              visit_weekdays="5", lead_time_days=2, delivery_days_set_at=None,
                              last_reminder_for=date(2026, 11, 6))

    async def remaining(session, store_id):
        return [following]

    monkeypatch.setattr(suppliers, "approved_suppliers", remaining)
    link = StoreSupplier(store_id=7, distributor_id=10, status="approved")
    session, shop = Session(link), store()
    assert await suppliers.end_supplier(session, shop, 10) == "approved"
    assert session.deleted == [link]
    # Se borran sus productos preferidos y el borrador del pedido con él.
    assert any("store_product_choices" in sql for sql in session.executed)
    assert any("pending_actions" in sql for sql in session.executed)
    assert (shop.distributor_id, shop.visit_weekdays, shop.lead_time_days) == (20, "5", 2)
    assert shop.delivery_days_set_at is None and shop.last_reminder_for == date(2026, 11, 6)


@pytest.mark.asyncio
async def test_leaving_the_only_supplier_leaves_the_store_without_one(monkeypatch):
    async def none(session, store_id):
        return []

    monkeypatch.setattr(suppliers, "approved_suppliers", none)
    shop = store()
    assert await suppliers.end_supplier(Session(StoreSupplier(status="approved")), shop, 10) == "approved"
    assert shop.distributor_id is None


@pytest.mark.asyncio
async def test_leaving_another_supplier_keeps_the_main_one():
    shop = store()
    assert await suppliers.end_supplier(Session(StoreSupplier(status="pending")), shop, 20) == "pending"
    assert shop.distributor_id == 10


@pytest.mark.asyncio
async def test_nothing_to_end():
    assert await suppliers.end_supplier(Session(None), store(), 20) is None
    rejected = Session(StoreSupplier(status="rejected"))
    assert await suppliers.end_supplier(rejected, store(), 20) is None and rejected.deleted == []


# --- Botones y textos ---------------------------------------------------------------------


def test_linked_suppliers_get_a_button_to_leave():
    markup = keyboards.providers_keyboard([option(3)], [], [option(1, "approved"), option(2, "pending")])
    buttons = [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]
    assert buttons[-2:] == [
        ("🚫 Dejar de trabajar con Proveedor 1", "provend:1"),
        ("↩️ Cancelar solicitud a Proveedor 2", "provend:2"),
    ]
    only_linked = keyboards.providers_keyboard([], [], [option(1, "approved")])
    assert [b.text for row in only_linked.inline_keyboard for b in row] == ["🚫 Dejar de trabajar con Proveedor 1"]


def test_texts():
    question = texts.end_supplier_question("Postobón", approved=True)
    assert "/recibido" in question and "/proveedores" in question and "inventario no se pierde" in question
    assert "Cancelar la solicitud" in texts.end_supplier_question("Postobón", approved=False)
    assert "/proveedores" in texts.supplier_ended("Postobón", True, remaining=0)
    assert "/catalogo" in texts.supplier_ended("Postobón", True, remaining=1)
    overview = texts.providers_overview("Bucaramanga", [option(1, "approved")])
    assert "🚫 Dejar de trabajar con" in overview


@pytest.fixture
def bot(monkeypatch):
    shop = SimpleNamespace(id=41, consent_at=datetime.now(UTC), city="Bucaramanga", distributor_id=1)
    replies: list[tuple[str, object]] = []
    ended: list[int] = []

    @asynccontextmanager
    async def fake_scope():
        yield None

    async def found(session, chat_id):
        return shop

    async def record(update, text, reply_markup=None):
        replies.append((text, reply_markup))

    async def end(session, store_, distributor_id):
        ended.append(distributor_id)
        return "approved"

    async def remaining(session, store_id):
        return []

    monkeypatch.setattr(handlers, "session_scope", fake_scope)
    monkeypatch.setattr(handlers, "get_store_by_chat", found)
    monkeypatch.setattr(handlers, "_reply", record)
    monkeypatch.setattr(handlers, "end_supplier", end)
    monkeypatch.setattr(handlers, "approved_suppliers", remaining)
    with_options(monkeypatch, [option(1, "approved"), option(2, "pending"), option(3)])
    return SimpleNamespace(replies=replies, ended=ended)


@pytest.mark.asyncio
async def test_leaving_asks_first_and_then_ends(bot):
    await handlers.handle_end_supplier(press("provend:1"), None)
    text, markup = bot.replies[-1]
    assert text == texts.end_supplier_question("Proveedor 1", approved=True)
    assert [b.callback_data for row in markup.inline_keyboard for b in row] == ["provend:1:yes", "provend:1:no"]
    assert bot.ended == []

    update = press("provend:1:yes")
    await handlers.handle_end_supplier(update, None)
    assert bot.ended == [1] and update.callback_query.removed
    assert bot.replies[-1][0] == texts.supplier_ended("Proveedor 1", True, 0)


@pytest.mark.asyncio
async def test_saying_no_keeps_the_supplier(bot):
    await handlers.handle_end_supplier(press("provend:2:no"), None)
    assert bot.ended == [] and bot.replies[-1][0] == texts.END_SUPPLIER_KEPT


@pytest.mark.asyncio
async def test_old_button_for_a_supplier_it_does_not_have(bot):
    await handlers.handle_end_supplier(press("provend:3:yes"), None)
    assert bot.ended == [] and bot.replies[-1][0] == texts.STALE_BUTTON


def test_pattern():
    import re

    for data in ("provend:1", "provend:12:yes", "provend:3:no"):
        assert re.match(keyboards.END_PATTERN, data)
    assert not re.match(keyboards.END_PATTERN, "provend:x:yes")
    assert isinstance(Query("x").data, str)
    assert provider_menu.MAX_OPTIONS >= 2
