"""/proveedores: quién llega a la tienda, la lista, los botones y las solicitudes."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.bot import handlers, keyboards, provider_menu, texts
from app.bot.provider_menu import ProviderOption, covers
from app.models import StoreSupplier


def distributor(**overrides):
    return SimpleNamespace(**{"coverage": None, "city": None, **overrides})


@pytest.mark.parametrize(
    "zone, city, expected",
    [
        ({"coverage": "Bucaramanga, Floridablanca, Girón"}, "GIRON", True),
        ({"coverage": "Bucaramanga, Floridablanca, Girón"}, "floridablanca", True),
        ({"coverage": "Bucaramanga; Piedecuesta."}, "Piedecuesta", True),
        ({"coverage": "Bucaramanga, Floridablanca"}, "Cúcuta", False),
        ({"coverage": "San Gil, Socorro"}, "Gil", False),  # palabra suelta, no parte de otra
        ({"city": "Bogotá"}, "bogota", True),
        ({"coverage": "Norte de Santander"}, "Santander", False),
        ({"coverage": "Bucaramanga y Girón / Lebrija"}, "lebrija", True),
        ({}, "Cúcuta", True),  # el proveedor no registró zona: se muestra
        ({"coverage": "Bucaramanga"}, None, True),  # la tienda no tiene ciudad
    ],
)
def test_covers(zone, city, expected):
    assert covers(distributor(**zone), city) is expected


def option(id_, status=None, **extra):
    return ProviderOption(id=id_, name=f"Proveedor {id_}", categories=["Bebidas y gaseosas"], products=12,
                          status=status, **extra)


def test_overview_shows_each_status():
    options = [option(1, "approved"), option(2, "pending"), option(3),
               option(4, "rejected", rejection_reason="No llego a tu barrio"), option(5, zone_known=False)]
    text = texts.providers_overview("Bucaramanga", options)
    assert "que llegan a Bucaramanga" in text
    assert "✅ Proveedor 1: ya trabajas" in text
    assert "⏳ Proveedor 2: tu solicitud está en revisión" in text
    assert "• Proveedor 3: Bebidas y gaseosas · 12 productos" in text
    assert "Antes no te aceptó: No llego a tu barrio" in text
    assert "no registró su zona" in text
    assert "Puedes escoger varios" in text


def test_overview_without_options_or_with_nothing_left():
    assert "no hay proveedores en TenderoBot que lleguen" in texts.providers_overview("Cúcuta", [])
    assert "no hay más proveedores" in texts.providers_overview(None, [option(1, "approved")])


def test_selectable_only_if_never_asked_or_rejected():
    assert [option(1, s).selectable for s in (None, "rejected", "pending", "approved")] == [True, True, False, False]


def test_keyboard_toggles_and_fits_telegram_limit():
    options = [option(9000 + i) for i in range(provider_menu.MAX_OPTIONS)]
    selected = [option.id for option in options]
    markup = keyboards.providers_keyboard(options, selected)
    datas = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert all(len(data.encode()) <= 64 for data in datas)
    assert markup.inline_keyboard[0][0].text.startswith("✅")
    assert datas[-1].endswith(":ok") and "(8)" in markup.inline_keyboard[-1][0].text
    assert keyboards.parse_providers_callback("prov:3,1:7") == ([3, 1], "7")
    assert keyboards.parse_providers_callback("prov::list") == ([], "list")
    assert keyboards.providers_keyboard([], []) is None


class LinkSession:
    def __init__(self, existing=None):
        self.existing = existing
        self.added = []

    async def scalar(self, statement):
        return self.existing

    def add(self, row):
        self.added.append(row)


def with_options(monkeypatch, options):
    async def fake(session, store):
        return options

    monkeypatch.setattr(provider_menu, "provider_options", fake)


@pytest.mark.asyncio
async def test_request_creates_pending_requests_only_for_selectable(monkeypatch):
    with_options(monkeypatch, [option(1), option(2, "pending"), option(3, "approved")])
    session = LinkSession()
    sent = await provider_menu.request_providers(session, SimpleNamespace(id=41), [1, 2, 3, 99])
    assert sent == ["Proveedor 1"]
    assert [(row.store_id, row.distributor_id, row.status) for row in session.added] == [(41, 1, "pending")]


@pytest.mark.asyncio
async def test_request_again_after_rejection_reopens_it(monkeypatch):
    with_options(monkeypatch, [option(4, "rejected", rejection_reason="No llego")])
    link = StoreSupplier(store_id=41, distributor_id=4, status="rejected", rejection_reason="No llego",
                         decided_at=datetime(2026, 11, 1, tzinfo=UTC), decision_notified_at=datetime.now(UTC))
    session = LinkSession(existing=link)
    assert await provider_menu.request_providers(session, SimpleNamespace(id=41), [4]) == ["Proveedor 4"]
    assert (link.status, link.rejection_reason, link.decided_at, link.decision_notified_at) == ("pending", None, None, None)
    assert session.added == []


# --- Botones en el bot ----------------------------------------------------------------


class Query:
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
def bot(monkeypatch):
    store = SimpleNamespace(id=41, consent_at=datetime.now(UTC), city="Bucaramanga", distributor_id=None)
    replies: list[tuple[str, object]] = []
    requested: list[list[int]] = []

    @asynccontextmanager
    async def fake_scope():
        yield None

    async def found(session, chat_id):
        return store

    async def record(update, text, reply_markup=None):
        replies.append((text, reply_markup))

    async def request(session, store_, chosen):
        requested.append(chosen)
        return [f"Proveedor {i}" for i in chosen]

    monkeypatch.setattr(handlers, "session_scope", fake_scope)
    monkeypatch.setattr(handlers, "get_store_by_chat", found)
    monkeypatch.setattr(handlers, "_reply", record)
    monkeypatch.setattr(provider_menu, "request_providers", request)
    with_options(monkeypatch, [option(1, "approved"), option(2), option(3)])
    return SimpleNamespace(replies=replies, requested=requested)


def press(data):
    return SimpleNamespace(callback_query=Query(data), effective_chat=SimpleNamespace(id=555),
                           effective_message=None)


@pytest.mark.asyncio
async def test_command_lists_providers_with_buttons_for_new_ones(bot):
    await handlers.handle_providers(SimpleNamespace(effective_chat=SimpleNamespace(id=555)), None)
    text, markup = bot.replies[0]
    assert "✅ Proveedor 1" in text
    buttons = [b.text for row in markup.inline_keyboard for b in row]
    assert buttons == ["⬜ Proveedor 2", "⬜ Proveedor 3", "📨 Enviar solicitudes", "🚫 Dejar de trabajar con Proveedor 1"]


@pytest.mark.asyncio
async def test_toggle_updates_buttons(bot):
    update = press("prov::3")
    await handlers.handle_providers_button(update, None)
    marked = [b.text for row in update.callback_query.markups[0].inline_keyboard for b in row]
    assert marked[:2] == ["⬜ Proveedor 2", "✅ Proveedor 3"]
    update = press("prov:3:1")  # el que ya es su proveedor no se puede marcar
    await handlers.handle_providers_button(update, None)
    assert "✅ Proveedor 1" not in str(update.callback_query.markups[0].inline_keyboard)


@pytest.mark.asyncio
async def test_send_requests_and_offer_more(bot):
    update = press("prov:2,3:ok")
    await handlers.handle_providers_button(update, None)
    assert bot.requested == [[2, 3]]
    assert update.callback_query.removed
    text, markup = bot.replies[-1]
    assert text == texts.providers_sent(["Proveedor 2", "Proveedor 3"])
    assert [b.callback_data for b in markup.inline_keyboard[0]] == ["prov::list", "prov::done"]


@pytest.mark.asyncio
async def test_send_without_selection_alerts(bot):
    update = press("prov::ok")
    await handlers.handle_providers_button(update, None)
    assert update.callback_query.alerts == [texts.PROVIDERS_PICK_ONE]
    assert bot.requested == []


@pytest.mark.asyncio
async def test_add_another_shows_list_and_done_closes(bot):
    await handlers.handle_providers_button(press("prov::list"), None)
    assert bot.replies[-1][0].startswith("🤝 Proveedores")
    await handlers.handle_providers_button(press("prov::done"), None)
    assert bot.replies[-1][0] == texts.PROVIDERS_DONE
