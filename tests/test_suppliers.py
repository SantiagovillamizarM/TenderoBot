"""Proveedores de la tienda: la copia del proveedor principal en StoreSupplier."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.models import StoreSupplier
from app.suppliers import mirror_store_link


class Session:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []
        self.flushed = 0

    async def scalar(self, statement):
        return self.existing

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        self.flushed += 1


def store(**overrides):
    base = dict(
        id=7, distributor_id=3, route_weekdays="0,2,4", visit_weekdays="0,4", lead_time_days=2,
        delivery_days_set_at=datetime(2026, 11, 1, tzinfo=UTC), last_reminder_for=date(2026, 11, 5),
    )
    return SimpleNamespace(**{**base, **overrides})


@pytest.mark.asyncio
async def test_creates_approved_link_with_store_days():
    session = Session()
    link = await mirror_store_link(session, store())
    assert session.added == [link]
    assert (link.store_id, link.distributor_id, link.status) == (7, 3, "approved")
    assert (link.route_weekdays, link.visit_weekdays, link.lead_time_days) == ("0,2,4", "0,4", 2)
    assert link.last_reminder_for == date(2026, 11, 5)
    assert link.decided_at is not None and link.decision_notified_at is not None


@pytest.mark.asyncio
async def test_updates_existing_link_without_adding():
    existing = StoreSupplier(store_id=7, distributor_id=3, status="approved", visit_weekdays="0,2")
    session = Session(existing)
    link = await mirror_store_link(session, store(visit_weekdays="2,4", last_reminder_for=date(2026, 11, 9)))
    assert link is existing and session.added == []
    assert (existing.visit_weekdays, existing.last_reminder_for) == ("2,4", date(2026, 11, 9))


@pytest.mark.asyncio
async def test_main_provider_of_the_store_is_always_approved():
    rejected = StoreSupplier(store_id=7, distributor_id=3, status="rejected", rejection_reason="No llego")
    await mirror_store_link(Session(rejected), store())
    assert (rejected.status, rejected.rejection_reason) == ("approved", None)


@pytest.mark.asyncio
async def test_store_without_provider_has_no_link():
    session = Session()
    assert await mirror_store_link(session, store(distributor_id=None)) is None
    assert session.added == []


@pytest.mark.asyncio
async def test_new_store_is_flushed_to_get_its_id():
    session = Session()
    new_store = store(id=None)

    async def flush():
        new_store.id = 42
        session.flushed += 1

    session.flush = flush
    link = await mirror_store_link(session, new_store)
    assert session.flushed == 1 and link.store_id == 42
