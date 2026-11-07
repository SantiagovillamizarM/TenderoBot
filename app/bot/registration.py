"""Registro de una tienda nueva desde Telegram (sin pasar por la página web).

1. El tendero comparte un número que no es de ninguna tienda: empieza el registro.
2. Primero el permiso de datos (Ley 1581): sin él no se pregunta nada.
3. Ocho preguntas, una por una, con las mismas reglas de la página (cédula, horario de reparto…):
   nombre de la tienda, dueño, documento, ciudad, barrio, dirección, horario y forma de pago.
   Luego la ubicación, opcional, con el botón de Telegram.
4. Resumen con ✅ Confirmar o ✏️ Empezar de nuevo. Al confirmar se crea la tienda SIN proveedores:
   el siguiente paso es escogerlos con /proveedores.

Lo respondido se guarda en BotRegistration: si cierra Telegram, al volver sigue en la misma pregunta.
Las funciones reciben `say(texto, botones)` para responder, así no dependen de Telegram.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import keyboards, texts
from app.hours import HoursError, hours_label, parse_hours_text
from app.models import BotRegistration, Store, StoreConsent
from app.web.catalog import FormError, parse_required
from app.web.store_forms import PAYMENT_METHODS, parse_document

Say = Callable[..., Awaitable[None]]

CONSENT = "consent"
CONFIRM = "confirm"
QUESTIONS = ["store_name", "owner_name", "document_id", "city", "neighborhood", "address", "receiving_hours",
             "payment_method", "location"]


def _parse_hours(raw: str) -> str:
    try:
        return parse_hours_text(raw)
    except HoursError as exc:
        raise FormError(str(exc)) from exc


# Respuestas escritas: (cómo se valida). Las demás llegan con botones (pago) o con la ubicación.
TEXT_ANSWERS: dict[str, Callable[[str], str]] = {
    "store_name": lambda raw: parse_required(raw, "el nombre de la tienda", 160),
    "owner_name": lambda raw: parse_required(raw, "tu nombre", 160),
    "document_id": parse_document,
    "city": lambda raw: parse_required(raw, "la ciudad", 80),
    "neighborhood": lambda raw: parse_required(raw, "el barrio", 120),
    "address": lambda raw: parse_required(raw, "la dirección", 200),
    "receiving_hours": _parse_hours,
}


async def get_registration(session: AsyncSession, chat_id: int) -> BotRegistration | None:
    return await session.get(BotRegistration, chat_id)


async def ask(registration: BotRegistration, say: Say) -> None:
    """Repite lo que toca en el paso actual (sirve para retomar el registro)."""
    step = registration.step
    if step == CONSENT:
        await say(texts.CONSENT_NOTICE, keyboards.consent_keyboard())
    elif step == CONFIRM:
        await say(summary(registration.data), keyboards.registration_confirm_keyboard())
    elif step == "payment_method":
        await say(texts.REG_QUESTIONS[step], keyboards.payment_keyboard())
    elif step == "location":
        await say(texts.REG_QUESTIONS[step], keyboards.location_keyboard())
    else:
        await say(texts.REG_QUESTIONS[step], keyboards.remove_keyboard())


def summary(data: dict) -> str:
    return texts.registration_summary(
        data, PAYMENT_METHODS.get(data.get("payment_method", ""), ""), hours_label(data.get("receiving_hours"))
    )


async def begin(session: AsyncSession, chat_id: int, phone: str, say: Say) -> BotRegistration:
    """Empieza (o reinicia) el registro de este chat con su número verificado."""
    await session.execute(delete(BotRegistration).where(BotRegistration.chat_id == chat_id))
    registration = BotRegistration(chat_id=chat_id, phone=phone, step=CONSENT, data={})
    session.add(registration)
    await session.flush()
    await say(texts.REG_WELCOME, keyboards.remove_keyboard())
    await ask(registration, say)
    return registration


async def _advance(registration: BotRegistration, key: str, value, say: Say) -> None:
    registration.data = {**registration.data, key: value}
    position = QUESTIONS.index(registration.step)
    registration.step = QUESTIONS[position + 1] if position + 1 < len(QUESTIONS) else CONFIRM
    await ask(registration, say)


async def accept_consent(registration: BotRegistration, say: Say) -> None:
    if registration.step != CONSENT:
        await ask(registration, say)  # botón viejo: sigue donde iba
        return
    registration.consent_at = datetime.now(UTC)
    registration.step = QUESTIONS[0]
    await say(texts.REG_START)
    await ask(registration, say)


async def reject_consent(session: AsyncSession, registration: BotRegistration, say: Say) -> None:
    await session.delete(registration)
    await say(texts.CONSENT_REJECTED, keyboards.share_phone_keyboard())


async def answer_text(registration: BotRegistration, raw: str, say: Say) -> None:
    step = registration.step
    if step == CONSENT:
        await say(texts.NEED_CONSENT, keyboards.consent_keyboard())
    elif step == CONFIRM:
        await say(texts.REG_CONFIRM_HINT, keyboards.registration_confirm_keyboard())
    elif step == "payment_method":
        await say(texts.REG_USE_BUTTONS, keyboards.payment_keyboard())
    elif step == "location":
        if raw.strip().casefold() in (keyboards.SKIP_LOCATION.casefold(), "no", "saltar"):
            await _advance(registration, "latitude", None, say)
        else:
            await say(texts.REG_LOCATION_HINT, keyboards.location_keyboard())
    else:
        try:
            value = TEXT_ANSWERS[step](raw)
        except FormError as exc:
            await say(f"{exc}\n\n{texts.REG_QUESTIONS[step]}")
            return
        await _advance(registration, step, value, say)


async def answer_location(registration: BotRegistration, latitude: float, longitude: float, say: Say) -> None:
    if registration.step != "location":
        await ask(registration, say)
        return
    registration.data = {
        **registration.data,
        "longitude": str(Decimal(str(longitude)).quantize(Decimal("0.000001"))),
    }
    await _advance(registration, "latitude", str(Decimal(str(latitude)).quantize(Decimal("0.000001"))), say)


async def answer_payment(registration: BotRegistration, method: str, say: Say) -> None:
    if registration.step != "payment_method" or method not in PAYMENT_METHODS:
        await ask(registration, say)
        return
    await _advance(registration, "payment_method", method, say)


async def restart(registration: BotRegistration, say: Say) -> None:
    registration.data = {}
    registration.step = QUESTIONS[0]
    await ask(registration, say)


async def cancel(session: AsyncSession, registration: BotRegistration, say: Say) -> None:
    await session.delete(registration)
    await say(texts.REG_CANCELLED, keyboards.share_phone_keyboard())


async def confirm(session: AsyncSession, registration: BotRegistration, say: Say) -> Store | None:
    """Crea la tienda (sin proveedores) con lo respondido y borra el registro."""
    if registration.step != CONFIRM:
        await ask(registration, say)
        return None
    if await session.scalar(select(Store.id).where(Store.owner_phone == registration.phone)) is not None:
        await session.delete(registration)
        await say(texts.REG_PHONE_TAKEN, keyboards.share_phone_keyboard())
        return None
    data = registration.data
    now = datetime.now(UTC)
    latitude = Decimal(data["latitude"]) if data.get("latitude") is not None else None
    longitude = Decimal(data["longitude"]) if latitude is not None and data.get("longitude") is not None else None
    store = Store(
        distributor_id=None,
        name=data["store_name"],
        owner_name=data["owner_name"],
        owner_phone=registration.phone,
        telegram_chat_id=registration.chat_id,
        consent_at=registration.consent_at or now,
        last_activity_at=now,
        document_id=data["document_id"],
        city=data["city"],
        neighborhood=data["neighborhood"],
        address=data["address"],
        receiving_hours=data["receiving_hours"],
        payment_method=data["payment_method"],
        latitude=latitude,
        longitude=longitude if latitude is not None else None,
    )
    session.add(store)
    await session.flush()
    session.add(StoreConsent(store_id=store.id, telegram_chat_id=registration.chat_id, accepted_at=store.consent_at))
    await session.delete(registration)
    await say(texts.registration_done(store.name), keyboards.remove_keyboard())
    return store
