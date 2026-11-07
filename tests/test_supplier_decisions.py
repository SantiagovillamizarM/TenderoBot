"""El proveedor acepta o rechaza a una tienda que lo escogió en Telegram, y el bot le avisa al tendero."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.bot import handlers, keyboards, supplier_notify, texts
from app.models import Store, StoreSupplier
from app.suppliers import approve_supplier, reject_supplier, set_supplier_days


def new_store(**overrides):
    values = dict(id=41, distributor_id=None, route_weekdays="0,2,4", visit_weekdays="0,3", lead_time_days=1,
                  delivery_days_set_at=None, telegram_chat_id=555, consent_at=datetime.now(UTC))
    values.update(overrides)
    return Store(**values)


def pending_link(distributor_id=3, **overrides):
    return StoreSupplier(id=12, store_id=41, distributor_id=distributor_id, status="pending",
                         requested_at=datetime.now(UTC), lead_time_days=1, **overrides)


# --- Aceptar, rechazar y días ---------------------------------------------------------


def test_first_provider_to_accept_becomes_the_main_one():
    store, link = new_store(), pending_link()
    approve_supplier(link, store, [4, 0, 2, 2], 2)
    assert (link.status, link.route_weekdays, link.visit_weekdays, link.lead_time_days) == ("approved", "0,2,4", "0,2,4", 2)
    assert link.decided_at is not None and link.decision_notified_at is None and link.delivery_days_set_at is None
    assert (store.distributor_id, store.route_weekdays, store.lead_time_days) == (3, "0,2,4", 2)
    assert store.delivery_days_set_at is None


def test_second_provider_does_not_replace_the_main_one():
    store = new_store(distributor_id=1, route_weekdays="1,3", delivery_days_set_at=datetime.now(UTC))
    link = pending_link(distributor_id=3)
    approve_supplier(link, store, [5], 1)
    assert link.status == "approved" and link.route_weekdays == "5"
    assert (store.distributor_id, store.route_weekdays) == (1, "1,3")
    assert store.delivery_days_set_at is not None


def test_reject_keeps_the_reason():
    link = pending_link()
    reject_supplier(link, "Nuestra ruta no llega a tu barrio")
    assert (link.status, link.rejection_reason) == ("rejected", "Nuestra ruta no llega a tu barrio")
    assert link.decided_at is not None and link.decision_notified_at is None


def test_days_with_main_provider_are_copied_to_the_store():
    store = new_store(distributor_id=3)
    link = pending_link(distributor_id=3)
    set_supplier_days(link, store, [4, 0])
    assert link.visit_weekdays == "0,4" and store.visit_weekdays == "0,4"
    assert store.delivery_days_set_at == link.delivery_days_set_at


def test_days_with_another_provider_stay_on_that_provider():
    store = new_store(distributor_id=1, visit_weekdays="1")
    link = pending_link(distributor_id=3)
    set_supplier_days(link, store, [5])
    assert link.visit_weekdays == "5" and store.visit_weekdays == "1"


# --- Aviso por Telegram --------------------------------------------------------------


def test_decision_messages():
    approved = pending_link()
    approved.status, approved.route_weekdays = "approved", "0,4"
    text, markup = supplier_notify.decision_message(approved, "Postobón")
    assert text == texts.supplier_approved("Postobón", [0, 4])
    assert "lunes y viernes" in text
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert datas == ["sdays:12::0", "sdays:12::4", "sdays:12::ok"]
    rejected = pending_link()
    rejected.status, rejected.rejection_reason = "rejected", "No llego"
    text, markup = supplier_notify.decision_message(rejected, "Postobón")
    assert "Motivo: No llego" in text and "/proveedores" in text and markup is None


class NotifySession:
    def __init__(self, link, store):
        self.link, self.store = link, store
        self.committed = False

    async def get(self, model, pk):
        return self.link if model is StoreSupplier else self.store

    async def scalar(self, statement):
        return "Distribuidora El Barrio"

    async def commit(self):
        self.committed = True


def patch_session(monkeypatch, session):
    @asynccontextmanager
    async def local():
        yield session

    monkeypatch.setattr(supplier_notify, "SessionLocal", local)


class Bot:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None):
        if self.fail:
            raise RuntimeError("Telegram no respondió")
        self.sent.append((chat_id, text, reply_markup))


@pytest.mark.asyncio
async def test_notify_once_and_mark_it(monkeypatch):
    link = pending_link()
    approve_supplier(link, new_store(), [0, 2], 1)
    session = NotifySession(link, new_store())
    patch_session(monkeypatch, session)
    bot = Bot()
    assert await supplier_notify.notify_supplier_decision(bot, 12) is True
    assert bot.sent[0][0] == 555 and "Distribuidora El Barrio aceptó" in bot.sent[0][1]
    assert link.decision_notified_at is not None and session.committed
    assert await supplier_notify.notify_supplier_decision(bot, 12) is False  # no avisa dos veces
    assert len(bot.sent) == 1


@pytest.mark.asyncio
async def test_failed_notice_is_left_for_the_retry(monkeypatch):
    link = pending_link()
    reject_supplier(link, "No llego")
    patch_session(monkeypatch, NotifySession(link, new_store()))
    assert await supplier_notify.notify_supplier_decision(Bot(fail=True), 12) is False
    assert link.decision_notified_at is None


@pytest.mark.asyncio
async def test_no_notice_while_pending_or_without_chat(monkeypatch):
    patch_session(monkeypatch, NotifySession(pending_link(), new_store()))
    assert await supplier_notify.notify_supplier_decision(Bot(), 12) is False
    link = pending_link()
    reject_supplier(link, "No llego")
    patch_session(monkeypatch, NotifySession(link, new_store(telegram_chat_id=None)))
    assert await supplier_notify.notify_supplier_decision(Bot(), 12) is False


# --- Escoger los días en el bot --------------------------------------------------------


class DaysQuery:
    def __init__(self, data):
        self.data = data
        self.alerts, self.markups = [], []
        self.removed = False

    async def answer(self, text=None, show_alert=False):
        if text:
            self.alerts.append(text)

    async def edit_message_reply_markup(self, reply_markup=None):
        if reply_markup is None:
            self.removed = True
        else:
            self.markups.append(reply_markup)


@pytest.fixture
def days_bot(monkeypatch):
    store = new_store(distributor_id=3)
    link = pending_link(distributor_id=3)
    approve_supplier(link, store, [0, 2, 4], 1)
    replies = []

    class Session:
        async def get(self, model, pk):
            return link if pk == 12 else None

        async def scalar(self, statement):
            return "Distribuidora El Barrio"

    @asynccontextmanager
    async def fake_scope():
        yield Session()

    async def found(session, chat_id):
        return store

    async def approved(session, store_id):
        return [link]

    async def record(update, text, reply_markup=None):
        replies.append(text)

    monkeypatch.setattr(handlers, "session_scope", fake_scope)
    monkeypatch.setattr(handlers, "get_store_by_chat", found)
    monkeypatch.setattr(handlers, "approved_suppliers", approved)
    monkeypatch.setattr(handlers, "_reply", record)
    return SimpleNamespace(store=store, link=link, replies=replies)


def press(data):
    return SimpleNamespace(callback_query=DaysQuery(data), effective_chat=SimpleNamespace(id=555),
                           effective_message=None)


@pytest.mark.asyncio
async def test_choose_days_with_a_provider(days_bot):
    update = press("sdays:12::4")
    await handlers.handle_supplier_days(update, None)
    labels = [b.text for row in update.callback_query.markups[0].inline_keyboard for b in row]
    assert labels[:3] == ["⬜ Lunes", "⬜ Miércoles", "✅ Viernes"]
    await handlers.handle_supplier_days(press("sdays:12:4:3"), None)  # jueves no es día de ruta
    update = press("sdays:12:04:ok")
    await handlers.handle_supplier_days(update, None)
    assert days_bot.link.visit_weekdays == "0,4" and days_bot.store.visit_weekdays == "0,4"
    assert days_bot.replies == [texts.supplier_days_saved("Distribuidora El Barrio", [0, 4], True)]
    assert update.callback_query.removed


@pytest.mark.asyncio
async def test_days_need_one_and_old_buttons_do_nothing(days_bot):
    update = press("sdays:12::ok")
    await handlers.handle_supplier_days(update, None)
    assert update.callback_query.alerts == [texts.DAYS_EMPTY]
    await handlers.handle_supplier_days(press("sdays:12:2:ok"), None)
    update = press("sdays:12:4:ok")
    await handlers.handle_supplier_days(update, None)
    assert days_bot.link.visit_weekdays == "2" and update.callback_query.removed


@pytest.mark.asyncio
async def test_days_button_of_another_store_is_stale(days_bot):
    days_bot.link.store_id = 99
    await handlers.handle_supplier_days(press("sdays:12::ok"), None)
    assert days_bot.replies == [texts.STALE_BUTTON]


def test_supplier_days_callback():
    assert keyboards.parse_supplier_days_callback("sdays:12:04:2") == (12, [0, 4], "2")


# --- Panel del proveedor ---------------------------------------------------------------


class PanelSession:
    def __init__(self, link, store):
        self.link, self.store = link, store
        self.committed = False

    async def get(self, model, pk):
        if model is StoreSupplier:
            return self.link if pk == self.link.id else None
        return self.store

    async def commit(self):
        self.committed = True

    async def rollback(self):
        return None


async def post_as_provider(path, data, session, distributor_id=3):
    from app.db import get_session
    from app.main import app
    from app.web.auth import require_distributor

    async def fake_session():
        yield session

    app.dependency_overrides[get_session] = fake_session
    app.dependency_overrides[require_distributor] = lambda: distributor_id
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(path, data=data)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_panel_accepts_a_telegram_request():
    session = PanelSession(pending_link(), new_store())
    response = await post_as_provider("/panel/solicitudes/tienda/12/aprobar",
                                      {"route_days": ["0", "3"], "lead_time_days": "2"}, session)
    assert response.status_code == 303 and response.headers["location"] == "/panel/solicitudes?ok=aceptada"
    assert (session.link.status, session.link.route_weekdays, session.link.lead_time_days) == ("approved", "0,3", 2)
    assert session.store.distributor_id == 3 and session.committed


@pytest.mark.asyncio
async def test_panel_rejects_with_reason():
    session = PanelSession(pending_link(), new_store())
    response = await post_as_provider("/panel/solicitudes/tienda/12/rechazar", {"reason": "No llego a tu barrio"}, session)
    assert response.status_code == 303
    assert (session.link.status, session.link.rejection_reason) == ("rejected", "No llego a tu barrio")


@pytest.mark.asyncio
async def test_provider_cannot_decide_another_providers_request():
    session = PanelSession(pending_link(distributor_id=8), new_store())
    response = await post_as_provider("/panel/solicitudes/tienda/12/aprobar",
                                      {"route_days": ["0"], "lead_time_days": "1"}, session, distributor_id=3)
    assert response.status_code == 404
    assert session.link.status == "pending" and not session.committed


@pytest.mark.asyncio
async def test_already_decided_request_is_not_decided_again():
    link = pending_link()
    reject_supplier(link, "No llego")
    session = PanelSession(link, new_store())
    response = await post_as_provider("/panel/solicitudes/tienda/12/aprobar",
                                      {"route_days": ["0"], "lead_time_days": "1"}, session)
    assert response.headers["location"] == "/panel/solicitudes"
    assert link.status == "rejected" and not session.committed
