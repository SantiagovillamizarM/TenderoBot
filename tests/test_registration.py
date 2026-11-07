"""Registro de una tienda nueva en Telegram: preguntas, validaciones, permiso de datos y la tienda creada."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.bot import handlers, keyboards, registration, texts
from app.hours import HoursError, parse_hours_text
from app.models import BotRegistration, Store, StoreConsent
from tests.test_bot_gate import message_update


# --- Horario escrito como lo dice un tendero ------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("de 7 a 5", "07:00-17:00"),
        ("8 am a 12 m", "08:00-12:00"),
        ("7:30 a 17:00", "07:30-17:00"),
        ("de 6 de la mañana a 2 de la tarde", "06:00-14:00"),
        ("1 a 6", "13:00-18:00"),
        ("7 a.m. a 4 p.m.", "07:00-16:00"),
    ],
)
def test_hours_text(text, expected):
    assert parse_hours_text(text) == expected


@pytest.mark.parametrize("text", ["todo el día", "de 12 a 3", "de 5 a 9 de la noche", "7"])
def test_hours_text_rejects_unclear_or_out_of_window(text):
    with pytest.raises(HoursError):
        parse_hours_text(text)


# --- Preguntas ----------------------------------------------------------------------


class Session:
    def __init__(self, store_with_phone=None):
        self.store_with_phone = store_with_phone
        self.added, self.deleted, self.executed = [], [], []

    def add(self, row):
        if isinstance(row, Store):
            row.id = 41
        self.added.append(row)

    async def delete(self, row):
        self.deleted.append(row)

    async def flush(self):
        return None

    async def execute(self, statement):
        self.executed.append(statement)

    async def scalar(self, statement):
        return self.store_with_phone


def recorder():
    said: list[tuple[str, object]] = []

    async def say(text, reply_markup=None):
        said.append((text, reply_markup))

    return said, say


ANSWERS = [
    ("store_name", "Tienda Doña Rosa", "Tienda Doña Rosa"),
    ("owner_name", "Rosa Pérez", "Rosa Pérez"),
    ("document_id", "1.098.765.432", "1098765432"),
    ("city", "Bucaramanga", "Bucaramanga"),
    ("neighborhood", "La Concordia", "La Concordia"),
    ("address", "Cl. 23 #4-50", "Cl. 23 #4-50"),
    ("receiving_hours", "de 7 a 5", "07:00-17:00"),
]


async def filled_registration() -> BotRegistration:
    signup = BotRegistration(chat_id=555, phone="573001234567", step=registration.CONSENT, data={})
    _, say = recorder()
    await registration.accept_consent(signup, say)
    for _step, answer, _saved in ANSWERS:
        await registration.answer_text(signup, answer, say)
    await registration.answer_payment(signup, "credito", say)
    await registration.answer_text(signup, keyboards.SKIP_LOCATION, say)
    return signup


@pytest.mark.asyncio
async def test_begin_asks_consent_before_any_question():
    session = Session()
    said, say = recorder()
    signup = await registration.begin(session, 555, "573001234567", say)
    assert signup.step == registration.CONSENT and signup in session.added
    assert [text for text, _ in said] == [texts.REG_WELCOME, texts.CONSENT_NOTICE]
    # Sin permiso no avanza: escribir algo vuelve a pedir el permiso.
    said.clear()
    await registration.answer_text(signup, "Tienda Doña Rosa", say)
    assert signup.step == registration.CONSENT and said[0][0] == texts.NEED_CONSENT


@pytest.mark.asyncio
async def test_questions_in_order_with_validation():
    signup = BotRegistration(chat_id=555, phone="573001234567", step=registration.CONSENT, data={})
    said, say = recorder()
    await registration.accept_consent(signup, say)
    assert signup.consent_at is not None
    assert said[-1][0] == texts.REG_QUESTIONS["store_name"]
    for step, answer, saved in ANSWERS:
        assert signup.step == step
        await registration.answer_text(signup, answer, say)
        assert signup.data[step] == saved
    assert signup.step == "payment_method"
    await registration.answer_text(signup, "de contado", say)  # se escoge con botones
    assert signup.step == "payment_method" and said[-1][0] == texts.REG_USE_BUTTONS
    await registration.answer_payment(signup, "credito", say)
    assert (signup.step, signup.data["payment_method"]) == ("location", "credito")
    await registration.answer_location(signup, 7.1193, -73.1227, say)
    assert signup.step == registration.CONFIRM
    assert (signup.data["latitude"], signup.data["longitude"]) == ("7.119300", "-73.122700")
    text, markup = said[-1]
    assert "Tienda Doña Rosa" in text and "A crédito" in text and "marcada en el mapa" in text
    assert markup.inline_keyboard[0][0].callback_data == keyboards.REG_CONFIRM


@pytest.mark.asyncio
@pytest.mark.parametrize("step, bad", [("document_id", "abc"), ("receiving_hours", "todo el día"),
                                       ("store_name", "   ")])
async def test_bad_answer_is_explained_and_asked_again(step, bad):
    signup = BotRegistration(chat_id=555, phone="5730", step=step, data={})
    said, say = recorder()
    await registration.answer_text(signup, bad, say)
    assert signup.step == step and step not in signup.data
    assert said[-1][0].endswith(texts.REG_QUESTIONS[step])


@pytest.mark.asyncio
async def test_confirm_creates_store_without_providers():
    signup = await filled_registration()
    session = Session()
    said, say = recorder()
    store = await registration.confirm(session, signup, say)
    assert isinstance(store, Store)
    assert store.distributor_id is None
    assert (store.owner_phone, store.telegram_chat_id) == ("573001234567", 555)
    assert (store.name, store.document_id, store.receiving_hours, store.payment_method) == (
        "Tienda Doña Rosa", "1098765432", "07:00-17:00", "credito")
    assert store.latitude is None and store.consent_at is not None
    consent = next(row for row in session.added if isinstance(row, StoreConsent))
    assert (consent.store_id, consent.telegram_chat_id) == (41, 555)
    assert session.deleted == [signup]
    assert said[-1][0] == texts.registration_done("Tienda Doña Rosa")


@pytest.mark.asyncio
async def test_confirm_with_location_saves_coordinates():
    signup = await filled_registration()
    signup.data = {**signup.data, "latitude": "7.119300", "longitude": "-73.122700"}
    store = await registration.confirm(Session(), signup, recorder()[1])
    assert (store.latitude, store.longitude) == (Decimal("7.119300"), Decimal("-73.122700"))


@pytest.mark.asyncio
async def test_confirm_refuses_phone_taken_meanwhile():
    signup = await filled_registration()
    session = Session(store_with_phone=9)
    said, say = recorder()
    assert await registration.confirm(session, signup, say) is None
    assert not any(isinstance(row, Store) for row in session.added)
    assert said[-1][0] == texts.REG_PHONE_TAKEN


@pytest.mark.asyncio
async def test_confirm_only_from_the_summary():
    signup = BotRegistration(chat_id=555, phone="5730", step="city", data={})
    session = Session()
    assert await registration.confirm(session, signup, recorder()[1]) is None
    assert session.added == []


@pytest.mark.asyncio
async def test_restart_cancel_and_reject():
    signup = await filled_registration()
    said, say = recorder()
    await registration.restart(signup, say)
    assert (signup.step, signup.data) == ("store_name", {})
    session = Session()
    await registration.cancel(session, signup, say)
    assert session.deleted == [signup] and said[-1][0] == texts.REG_CANCELLED
    session = Session()
    await registration.reject_consent(session, signup, say)
    assert session.deleted == [signup] and said[-1][0] == texts.CONSENT_REJECTED


# --- El candado de entrada durante el registro ------------------------------------------


@pytest.fixture
def signing_up(monkeypatch):
    """Chat sin tienda que está en la pregunta de la ciudad."""
    signup = BotRegistration(chat_id=555, phone="573001234567", step="city", data={})
    replies: list[str] = []

    @asynccontextmanager
    async def fake_scope():
        yield Session()

    async def no_store(session, chat_id):
        return None

    async def current(session, chat_id):
        return signup

    async def record(update, text, reply_markup=None):
        replies.append(text)

    monkeypatch.setattr(handlers, "session_scope", fake_scope)
    monkeypatch.setattr(handlers, "get_store_by_chat", no_store)
    monkeypatch.setattr(registration, "get_registration", current)
    monkeypatch.setattr(handlers, "_reply", record)
    return SimpleNamespace(signup=signup, replies=replies)


@pytest.mark.asyncio
async def test_text_during_signup_answers_the_question(signing_up):
    from telegram.ext import ApplicationHandlerStop

    with pytest.raises(ApplicationHandlerStop):
        await handlers.require_link(message_update("Bucaramanga"), None)
    assert signing_up.signup.data == {"city": "Bucaramanga"}
    assert signing_up.replies == [texts.REG_QUESTIONS["neighborhood"]]


@pytest.mark.asyncio
async def test_other_commands_wait_and_cancel_stops(signing_up):
    from telegram.ext import ApplicationHandlerStop

    with pytest.raises(ApplicationHandlerStop):
        await handlers.require_link(message_update("/pedido"), None)
    assert signing_up.replies == [texts.REG_FINISH_FIRST]
    with pytest.raises(ApplicationHandlerStop):
        await handlers.require_link(message_update("/cancelar"), None)
    assert signing_up.replies[-1] == texts.REG_CANCELLED


@pytest.mark.asyncio
async def test_start_passes_to_its_handler_and_resumes(signing_up):
    await handlers.require_link(message_update("/start"), None)  # no se detiene
    await handlers.handle_start(message_update("/start"), None)
    assert signing_up.replies == [texts.INTRO, texts.REG_QUESTIONS["city"]]


@pytest.mark.asyncio
async def test_store_without_providers_is_sent_to_choose_them(monkeypatch):
    store = SimpleNamespace(id=41, consent_at=datetime.now(UTC), distributor_id=None, delivery_days_set_at=None)
    replies: list[str] = []

    async def found(session, chat_id):
        return store

    async def record(update, text, reply_markup=None):
        replies.append(text)

    monkeypatch.setattr(handlers, "get_store_by_chat", found)
    monkeypatch.setattr(handlers, "_reply", record)
    assert await handlers.require_ready_store(message_update("vendí 2 cocas"), None) is None
    assert replies == [texts.NO_SUPPLIERS]
