"""Un pedido por proveedor: planes del motor, borradores separados y cambios al pedido de cada uno."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.bot import handlers, texts
from app.inventory.engine import COLOMBIA_TZ, ProductInput, SaleEvent, analyze_store
from app.schemas import OrderChange

# Martes 10 de noviembre de 2026, 9 a. m. en Colombia.
NOW = datetime(2026, 11, 10, 9, 0, tzinfo=COLOMBIA_TZ)


def daily_sales(product_id: int, units: int, days: int = 28) -> list[SaleEvent]:
    return [SaleEvent(product_id, units, Decimal("1000"), NOW - timedelta(days=d, hours=1)) for d in range(1, days + 1)]


def test_each_supplier_gets_its_own_plan_with_its_days():
    products = [
        ProductInput(1, "COCA", "Coca-Cola 1 litro", 12, Decimal("4500"), distributor_id=10),
        ProductInput(2, "PONY", "Pony Malta", 24, Decimal("2500"), distributor_id=20),
    ]
    analysis = analyze_store(
        store_id=7, visit_weekdays="0", lead_time_days=1, products=products,
        sales=daily_sales(1, 5) + daily_sales(2, 5), counts=[], receipts=[], now=NOW,
        schedules={10: ("3", 1), 20: ("5", 2)},  # El Barrio entrega los jueves; Postobón los sábados
        primary_distributor_id=10,
    )
    assert [plan.distributor_id for plan in analysis.plans] == [10, 20]  # el principal primero
    barrio, postobon = analysis.plans
    assert barrio.next_delivery.weekday() == 3 and postobon.next_delivery.weekday() == 5
    assert [line.product_id for line in barrio.order_lines] == [1]
    assert [line.product_id for line in postobon.order_lines] == [2]
    # Compatibilidad: lo de la tienda es lo del principal.
    assert analysis.next_delivery == barrio.next_delivery and analysis.order_lines == barrio.order_lines
    assert analysis.plan_for(20) is postobon and analysis.plan_for(99) is None
    assert {p.product_id for p in analysis.products} == {1, 2}


def test_without_suppliers_it_works_as_before():
    products = [ProductInput(1, "COCA", "Coca-Cola 1 litro", 12, Decimal("4500"))]
    analysis = analyze_store(7, "0,3", 1, products, daily_sales(1, 5), [], [], NOW)
    assert len(analysis.plans) == 1 and analysis.plans[0].distributor_id is None
    assert analysis.order_lines == analysis.plans[0].order_lines


def test_store_without_products_still_has_a_plan():
    analysis = analyze_store(7, "0,3", 1, [], [], [], [], NOW, schedules={10: ("3", 1)}, primary_distributor_id=10)
    assert [plan.distributor_id for plan in analysis.plans] == [10] and analysis.order_lines == []


def test_order_texts_name_the_supplier():
    delivery = NOW.date()
    assert texts.order_draft([], 0, delivery, NOW, supplier="Postobón").startswith(
        "📦 Por ahora no necesitas pedirle nada a Postobón")
    assert "Pedido de Postobón del" in texts.receipt_summary([], delivery, "Postobón")
    assert "mercancía de Postobón" in texts.order_reminder(delivery, NOW, "Postobón")
    assert "Postobón" not in texts.order_reminder(delivery, NOW)
    assert handlers.order_kind(5) == "order:5" and handlers.order_kind(None) == "order"


# --- Cambios al pedido: cada producto va al pedido de su proveedor ------------------------

COCA = SimpleNamespace(id=1, sku="COCA", name="Coca-Cola 1 litro", aliases="", presentation="1 L", distributor_id=10,
                       unit_price=Decimal("4500"), units_per_case=12, available_units=None)
PONY = SimpleNamespace(id=2, sku="PONY", name="Pony Malta", aliases="", presentation="330 ml", distributor_id=20,
                       unit_price=Decimal("2500"), units_per_case=24, available_units=None)


def draft(distributor_id, lines):
    return SimpleNamespace(id=50 + distributor_id, kind=handlers.order_kind(distributor_id), payload={
        "distributor_id": distributor_id, "lines": lines, "delivery_date": "2026-11-12",
        "order_deadline": "2026-11-11T18:00:00-05:00"})


@pytest.fixture
def two_drafts(monkeypatch):
    drafts = {"order:10": draft(10, [{"product_id": 1, "cases": 1, "reason": "x"}]), "order:20": None}
    saved = []

    @asynccontextmanager
    async def fake_scope():
        class Session:
            async def get(self, model, pk):
                return SimpleNamespace(name={10: "El Barrio", 20: "Postobón"}[pk]) if pk in (10, 20) else None

        yield Session()

    async def pending(session, store_id, kind):
        return drafts.get(kind)

    async def replace(session, store_id, kind, payload, ttl=None):
        saved.append((kind, payload))
        return SimpleNamespace(id=len(saved), kind=kind, payload=payload)

    async def name(session, distributor_id):
        return {10: "El Barrio", 20: "Postobón"}.get(distributor_id)

    monkeypatch.setattr(handlers, "session_scope", fake_scope)
    monkeypatch.setattr(handlers, "get_pending", pending)
    monkeypatch.setattr(handlers, "replace_pending", replace)
    monkeypatch.setattr(handlers, "supplier_name", name)
    return SimpleNamespace(drafts=drafts, saved=saved)


@pytest.mark.asyncio
async def test_change_goes_to_the_order_of_that_supplier(two_drafts):
    changes = [OrderChange(sku="COCA", product_text="coca", action="add", quantity=2)]
    messages, info = await handlers.change_open_order(7, changes, [COCA, PONY])
    assert [kind for kind, _payload in two_drafts.saved] == ["order:10"]
    assert two_drafts.saved[0][1]["lines"] == [{"product_id": 1, "cases": 3, "reason": "x"}]
    assert len(messages) == 1 and "Pedido a El Barrio" in messages[0].text
    assert info["borrador"] == "actualizado" and info["proveedor"] == "El Barrio"


@pytest.mark.asyncio
async def test_change_for_supplier_without_open_order_is_reported(two_drafts):
    changes = [OrderChange(sku="COCA", product_text="coca", action="add", quantity=1),
               OrderChange(sku="PONY", product_text="pony", action="add", quantity=1)]
    messages, info = await handlers.change_open_order(7, changes, [COCA, PONY])
    assert len(messages) == 1  # solo el de El Barrio estaba abierto
    assert info["sin_pedido_abierto"] == ["Postobón"]
    two_drafts.saved.clear()
    messages, info = await handlers.change_open_order(7, changes[1:], [COCA, PONY])
    assert "Postobón" in info["error"] and two_drafts.saved == []


@pytest.mark.asyncio
async def test_changes_to_two_open_orders(two_drafts):
    two_drafts.drafts["order:20"] = draft(20, [])
    changes = [OrderChange(sku="COCA", product_text="coca", action="remove", quantity=0),
               OrderChange(sku="PONY", product_text="pony", action="add", quantity=2)]
    messages, info = await handlers.change_open_order(7, changes, [COCA, PONY])
    assert sorted(kind for kind, _ in two_drafts.saved) == ["order:10", "order:20"]
    assert len(messages) == 2 and len(info["pedidos"]) == 2
    payloads = dict(two_drafts.saved)
    assert payloads["order:10"]["lines"] == []
    assert payloads["order:20"]["lines"][0]["product_id"] == 2


def test_order_drafts_of_any_supplier_count_as_order():
    clause = str(handlers._kind_filter(handlers.KIND_ORDER).compile(compile_kwargs={"literal_binds": True}))
    assert "order:%" in clause
    assert str(handlers._kind_filter("sales").compile(compile_kwargs={"literal_binds": True})).endswith("'sales'")
