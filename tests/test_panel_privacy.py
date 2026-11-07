"""Panel del proveedor con tiendas que tienen varios proveedores: cada uno ve solo lo suyo."""

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from app.inventory.engine import COLOMBIA_TZ, StoreAnalysis, SupplierPlan
from app.web import routes


def analysis():
    deadline = datetime(2026, 11, 10, 18, tzinfo=COLOMBIA_TZ)
    barrio = SupplierPlan(10, date(2026, 11, 12), deadline, date(2026, 11, 16), 6,
                          [SimpleNamespace(product_id=1, subtotal=100)])
    postobon = SupplierPlan(20, date(2026, 11, 14), deadline, date(2026, 11, 21), 11,
                            [SimpleNamespace(product_id=2, subtotal=200)])
    products = [SimpleNamespace(product_id=1, at_risk=True, stagnant=False),
                SimpleNamespace(product_id=2, at_risk=False, stagnant=True)]
    return StoreAnalysis(7, deadline, 6, barrio.next_delivery, deadline, products, barrio.order_lines,
                         [barrio, postobon])


def test_provider_sees_only_its_products_and_its_plan():
    scoped = routes._scoped(analysis(), {2}, 20)
    assert [p.product_id for p in scoped.products] == [2]
    assert [line.product_id for line in scoped.order_lines] == [2]
    assert scoped.next_delivery == date(2026, 11, 14) and scoped.horizon_days == 11
    assert scoped.at_risk == [] and len(scoped.stagnant) == 1
    assert [plan.distributor_id for plan in scoped.plans] == [20]


def test_provider_without_plan_sees_no_order():
    scoped = routes._scoped(analysis(), set(), 99)
    assert scoped.products == [] and scoped.order_lines == [] and scoped.plans == []


@pytest.mark.asyncio
async def test_serves_main_provider_and_accepted_ones_only():
    class Session:
        def __init__(self, found):
            self.found = found

        async def scalar(self, statement):
            return self.found

    store = SimpleNamespace(id=7, distributor_id=10)
    assert await routes._serves(Session(None), 10, store) is True  # principal
    assert await routes._serves(Session(5), 20, store) is True  # lo aceptó
    assert await routes._serves(Session(None), 20, store) is False  # no lo aceptó
    assert await routes._serves(Session(None), 10, None) is False
