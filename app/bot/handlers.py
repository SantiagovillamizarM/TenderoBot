import logging
import math
import re
from collections import Counter, defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from time import monotonic

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from app.ai.agent import AgentResult, run_agent
from app.ai.grok_client import (
    ExtractError,
    extract_order_changes,
    extract_sales,
    extract_stock,
    transcribe_audio,
)
from app.ai.matching import best_product_match, match_order_changes, match_sale_lines, match_stock_lines
from app.ai.prompts import AGENT_SYSTEM_PROMPT
from app.ai.tools import StoreActions, build_tools
from app.bot import keyboards, provider_menu, registration, texts
from app.config import get_settings
from app.db import SessionLocal
from app.inventory.engine import delivery_schedule, load_store_analysis, parse_visit_weekdays
from app.inventory.orders import apply_order_changes, cap_to_available, order_total, to_order_lines
from app.models import (
    SUPPLIER_APPROVED,
    Distributor,
    PendingAction,
    Product,
    Receipt,
    Sale,
    StockCount,
    Store,
    StoreConsent,
    SuggestedOrder,
    StoreRequest,
    SuggestedOrderItem,
    StoreSupplier,
)

from app.phones import format_phone, normalize_phone
from app.schemas import OrderChange, SaleLine, StockLine
from app import catalog_merge
from app.store_catalog import choose, load_items, store_products
from app.suppliers import approved_suppliers, end_supplier, mirror_store_link, set_supplier_days
from app.usage_log import logged_task, set_task_info

RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW_SECONDS = 60.0
MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_VOICE_SECONDS = 120
PENDING_TTL = timedelta(minutes=15)
# El borrador del pedido dura más: el tendero puede ir ajustándolo durante el día.
ORDER_PENDING_TTL = timedelta(hours=6)

_chat_hits: dict[int, deque[float]] = defaultdict(deque)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except ApplicationHandlerStop:
            # Detener la cadena de handlers no es un error: hay que guardar lo hecho.
            await session.commit()
            raise
        except Exception:
            await session.rollback()
            raise


def _chat_id(update: Update) -> int | None:
    chat = update.effective_chat
    if chat is None:
        return None
    return chat.id


def _is_rate_limited(chat_id: int) -> bool:
    now = monotonic()
    hits = _chat_hits[chat_id]
    while hits and now - hits[0] >= RATE_LIMIT_WINDOW_SECONDS:
        hits.popleft()
    if len(hits) >= RATE_LIMIT_MAX:
        return True
    hits.append(now)
    return False


async def _reply(update: Update, text: str, reply_markup=None) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(text, reply_markup=reply_markup)


async def _ask_login(update: Update, text: str = texts.ASK_LOGIN) -> None:
    await _reply(update, text, reply_markup=keyboards.share_phone_keyboard())


async def _ask_account(update: Update, text: str = texts.ASK_ACCOUNT) -> None:
    """Chat sin tienda: primero se pregunta si ya tiene cuenta o si va a registrar su tienda."""
    await _reply(update, text, reply_markup=keyboards.account_keyboard())


# Lo que escogió cada chat sin tienda en /start ("login" o "signup") y el número que ya verificó.
# En memoria: si el bot se reinicia, basta con volver a escoger.
_start_choice: dict[int, str] = {}
_verified_phone: dict[int, str] = {}


async def check_rate_limit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    if not _is_rate_limited(chat_id):
        return
    query = update.callback_query
    if query is not None:
        await query.answer(texts.RATE_LIMITED, show_alert=True)
    else:
        await _reply(update, texts.RATE_LIMITED)
    raise ApplicationHandlerStop


async def require_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mientras el chat no tenga tienda, solo se aceptan /start y el número compartido.

    Con tienda vinculada, cada mensaje o botón cuenta como actividad (aviso de sesión abierta).
    """
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is not None:
            store.last_activity_at = datetime.now(UTC)
            return
        signup = await registration.get_registration(session, chat_id)
        if signup is not None and await handle_registration_update(update, session, signup):
            raise ApplicationHandlerStop
        if signup is not None:
            return  # botones del registro, /start o el número compartido: los atienden sus handlers
        in_review = await has_request_in_review(session, chat_id)
    message = update.message
    if message is not None and message.contact is not None:
        return
    if message is not None and message.text and message.text.split()[0].split("@")[0].lower() == "/start":
        return
    query = update.callback_query
    if query is not None and re.match(keyboards.START_PATTERN, query.data or ""):
        return
    if query is not None:
        await query.answer()
    if in_review:
        await _reply(update, texts.REQUEST_IN_REVIEW)
    else:
        await _ask_account(update)
    raise ApplicationHandlerStop


def _say(update: Update):
    async def say(text: str, reply_markup=None) -> None:
        await _reply(update, text, reply_markup=reply_markup)

    return say


async def handle_registration_update(update: Update, session: AsyncSession, signup) -> bool:
    """Un chat que está registrando su tienda. True si el mensaje ya quedó atendido aquí.

    Pasan a sus handlers (devuelve False): los botones, /start y el número compartido.
    """
    message = update.message
    if update.callback_query is not None or message is None or message.contact is not None:
        return False
    say = _say(update)
    if message.location is not None:
        await registration.answer_location(signup, message.location.latitude, message.location.longitude, say)
        return True
    text = (message.text or "").strip()
    if not text:
        await registration.ask(signup, say)
        return True
    if text.startswith("/"):
        command = text.split()[0].split("@")[0].lower()
        if command == "/start":
            return False
        if command == "/cancelar":
            await registration.cancel(session, signup, say)
        else:
            await say(texts.REG_FINISH_FIRST)
        return True
    await registration.answer_text(signup, text, say)
    return True


async def handle_registration_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Botones del registro: forma de pago, ✅ Confirmar y ✏️ Empezar de nuevo."""
    await _answer_callback(update)
    chat_id = _chat_id(update)
    query = update.callback_query
    if chat_id is None or query is None:
        return
    say = _say(update)
    async with session_scope() as session:
        signup = await registration.get_registration(session, chat_id)
        if signup is None:
            await _stale_button(update)
            return
        data = query.data or ""
        if data.startswith("reg:pay:"):
            await registration.answer_payment(signup, data.rsplit(":", 1)[1], say)
        elif data == keyboards.REG_RESTART:
            await registration.restart(signup, say)
        else:
            store = await registration.confirm(session, signup, say)
            if store is not None:
                await _remove_buttons(update)
                await show_providers(update, session, store)
                return
    await _remove_buttons(update)


async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Durante el registro, /cancelar lo atiende require_link. Aquí solo llega un chat con tienda.
    await _reply(update, texts.NOTHING_TO_CANCEL)


async def show_providers(update: Update, session: AsyncSession, store: Store) -> None:
    """Lista de proveedores que llegan a la tienda, con botones para pedirles trabajar juntos."""
    options = await provider_menu.provider_options(session, store)
    selectable = [option for option in options if option.selectable][: provider_menu.MAX_OPTIONS]
    await _reply(
        update,
        texts.providers_overview(store.city, options),
        reply_markup=keyboards.providers_keyboard(selectable, [], _linked(options)),
    )


def _linked(options: list) -> list:
    """Proveedores con los que trabaja o a los que les pidió: se pueden dejar desde /proveedores."""
    return [option for option in options if option.status in ("approved", "pending")][: provider_menu.MAX_OPTIONS]


async def handle_providers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/proveedores: también para una tienda que todavía no tiene ninguno."""
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None:
            await _ask_login(update)
            return
        if store.consent_at is None:
            await _reply(update, texts.NEED_CONSENT, reply_markup=keyboards.consent_keyboard())
            return
        await show_providers(update, session, store)


async def handle_providers_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Marcar/desmarcar proveedores, enviar las solicitudes, agregar otro o terminar."""
    query = update.callback_query
    chat_id = _chat_id(update)
    if query is None or chat_id is None or not query.data:
        return
    selected, action = keyboards.parse_providers_callback(query.data)
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None or store.consent_at is None:
            await query.answer()
            await _ask_login(update)
            return
        if action == "done":
            await query.answer()
            await _remove_buttons(update)
            await _reply(update, texts.PROVIDERS_DONE)
            return
        if action == "list":
            await query.answer()
            await _remove_buttons(update)
            await show_providers(update, session, store)
            return
        options = await provider_menu.provider_options(session, store)
        selectable = [option for option in options if option.selectable][: provider_menu.MAX_OPTIONS]
        allowed = {option.id for option in selectable}
        selected = [i for i in selected if i in allowed]
        if action != "ok":
            chosen = int(action)
            selected = sorted(set(selected) ^ {chosen}) if chosen in allowed else selected
            await query.answer()
            try:
                await query.edit_message_reply_markup(
                    reply_markup=keyboards.providers_keyboard(selectable, selected, _linked(options))
                )
            except Exception:
                logger.info("No se pudieron actualizar los botones de proveedores")
            return
        if not selected:
            await query.answer(texts.PROVIDERS_PICK_ONE, show_alert=True)
            return
        await query.answer()
        sent = await provider_menu.request_providers(session, store, selected)
    await _remove_buttons(update)
    if sent:
        await _reply(update, texts.providers_sent(sent), reply_markup=keyboards.providers_more_keyboard())
    else:
        await _reply(update, texts.STALE_BUTTON)


async def handle_end_supplier(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """🚫 Dejar de trabajar con un proveedor (o cancelar la solicitud): primero se confirma."""
    query = update.callback_query
    chat_id = _chat_id(update)
    if query is None or chat_id is None or not query.data:
        return
    await query.answer()
    parts = query.data.split(":")
    distributor_id = int(parts[1])
    answer = parts[2] if len(parts) > 2 else None
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None or store.consent_at is None:
            await _ask_login(update)
            return
        option = next(
            (o for o in await provider_menu.provider_options(session, store)
             if o.id == distributor_id and o.status in ("approved", "pending")),
            None,
        )
        if option is None:
            await _remove_buttons(update)
            await _reply(update, texts.STALE_BUTTON)
            return
        approved = option.status == "approved"
        if answer is None:
            await _reply(
                update,
                texts.end_supplier_question(option.name, approved),
                reply_markup=keyboards.end_supplier_keyboard(option.id, option.name),
            )
            return
        await _remove_buttons(update)
        if answer == "no":
            await _reply(update, texts.END_SUPPLIER_KEPT)
            return
        await end_supplier(session, store, distributor_id)
        remaining = len(await approved_suppliers(session, store.id))
        await _reply(update, texts.supplier_ended(option.name, approved, remaining))


async def has_request_in_review(session: AsyncSession, chat_id: int) -> bool:
    """¿Este chat pidió acceso desde la página y su distribuidor aún no responde?"""
    found = await session.scalar(
        select(StoreRequest.id).where(StoreRequest.telegram_chat_id == chat_id, StoreRequest.status == "pending")
    )
    return found is not None


async def find_request_by_phone(session: AsyncSession, phone: str) -> StoreRequest | None:
    """La solicitud más reciente de ese celular (en revisión o rechazada)."""
    return await session.scalar(
        select(StoreRequest)
        .where(StoreRequest.phone == phone, StoreRequest.status.in_(("pending", "rejected")))
        .order_by(StoreRequest.id.desc())
    )


async def get_store_by_chat(session: AsyncSession, chat_id: int) -> Store | None:
    return await session.scalar(select(Store).where(Store.telegram_chat_id == chat_id))


async def require_ready_store(update: Update, session: AsyncSession) -> Store | None:
    chat_id = _chat_id(update)
    if chat_id is None:
        return None
    store = await get_store_by_chat(session, chat_id)
    if store is None:
        await _ask_login(update)
        return None
    if store.consent_at is None:
        await _reply(update, texts.NEED_CONSENT, reply_markup=keyboards.consent_keyboard())
        return None
    if store.distributor_id is None:
        # Se registró en Telegram y todavía ningún proveedor la aceptó: no hay catálogo ni pedido.
        await _reply(update, texts.NO_SUPPLIERS)
        return None
    if store.delivery_days_set_at is None:
        await _reply(update, texts.NEED_DAYS)
        await ask_days(update, store)
        return None
    if await session.scalar(select(Distributor.status).where(Distributor.id == store.distributor_id)) != "active":
        await _reply(update, texts.PROVIDER_SUSPENDED)
        return None
    link = await supplier_without_days(session, store)
    if link is not None:
        # Otro proveedor la aceptó y el tendero no ha escogido sus días de entrega con él.
        route = parse_visit_weekdays(link.route_weekdays or "") or list(range(7))
        name = await supplier_name(session, link.distributor_id) or "tu proveedor"
        await _reply(update, texts.need_supplier_days(name, route),
                     reply_markup=keyboards.supplier_days_keyboard(link.id, route, []))
        return None
    return store


async def supplier_without_days(session: AsyncSession, store: Store) -> StoreSupplier | None:
    """Un proveedor aceptado (que no es el principal) con el que el tendero aún no escoge días."""
    return await session.scalar(
        select(StoreSupplier)
        .where(
            StoreSupplier.store_id == store.id,
            StoreSupplier.status == SUPPLIER_APPROVED,
            StoreSupplier.delivery_days_set_at.is_(None),
            StoreSupplier.distributor_id != store.distributor_id,
        )
        .order_by(StoreSupplier.decided_at, StoreSupplier.id)
        .limit(1)
    )


def route_days(store: Store) -> list[int]:
    """Días en que el proveedor pasa por la zona. Sin configurar: toda la semana."""
    return parse_visit_weekdays(store.route_weekdays) or list(range(7))


async def ask_days(update: Update, store: Store) -> None:
    days = route_days(store)
    await _reply(update, texts.ask_days(days), reply_markup=keyboards.days_keyboard(days, []))


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None:
            signup = await registration.get_registration(session, chat_id)
            if signup is not None:
                # Está registrando su tienda: retoma la pregunta en la que iba.
                await _reply(update, texts.INTRO)
                await registration.ask(signup, _say(update))
                return
            await _reply(update, texts.START_UNLINKED, reply_markup=keyboards.account_keyboard())
            return
        # Siempre se saluda primero y luego se retoma el paso que falte.
        if store.consent_at is None:
            await _reply(update, texts.INTRO)
            await _reply(update, texts.CONSENT_NOTICE, reply_markup=keyboards.consent_keyboard())
            return
        if store.distributor_id is None:
            await _reply(update, texts.INTRO + "\n\n" + texts.NO_SUPPLIERS)
            return
        if store.delivery_days_set_at is None:
            await _reply(update, texts.start_needs_days(store.name))
            await ask_days(update, store)
            return
        await _reply(update, texts.START_LINKED)


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entrada con el botón 📱: Telegram manda el número verificado de la cuenta que escribe."""
    chat_id = _chat_id(update)
    message = update.effective_message
    user = update.effective_user
    if chat_id is None or message is None or message.contact is None:
        return
    # Con el botón solo se puede mandar el propio número; un contacto reenviado trae otro user_id.
    if user is None or message.contact.user_id != user.id:
        await _ask_login(update, texts.CONTACT_NOT_YOURS)
        return
    phone = normalize_phone(message.contact.phone_number)
    previous_chat_id = None
    async with session_scope() as session:
        existing = await get_store_by_chat(session, chat_id)
        if existing is not None:
            await _reply(update, texts.already_logged_in(existing.name), reply_markup=keyboards.remove_keyboard())
            return
        store = await session.scalar(select(Store).where(Store.owner_phone == phone)) if phone else None
        if store is None:
            request = await find_request_by_phone(session, phone) if phone else None
            if request is not None and request.status == "pending":
                # Pidió acceso en la página: se guarda el chat para avisarle aquí cuando decidan.
                request.telegram_chat_id = chat_id
                await _reply(update, texts.REQUEST_IN_REVIEW, reply_markup=keyboards.remove_keyboard())
            elif not phone:
                await _ask_login(update, texts.phone_not_registered())
            else:
                if request is not None:
                    # Su solicitud de la página no fue aprobada: puede registrarse aquí y escoger proveedores.
                    await _reply(update, texts.request_rejected(request.store_name, request.rejection_reason or ""))
                signup = await registration.get_registration(session, chat_id)
                if signup is not None and signup.phone == phone:
                    await registration.ask(signup, _say(update))
                elif _start_choice.get(chat_id) == "signup":
                    await registration.begin(session, chat_id, phone, _say(update))
                else:
                    # Dijo que ya tenía cuenta (o no escogió): su número no es de ninguna tienda.
                    _verified_phone[chat_id] = phone
                    await _reply(
                        update, texts.no_account_for_phone(format_phone(phone)), reply_markup=keyboards.register_keyboard()
                    )
            return
        if store.telegram_chat_id is not None:
            # El dueño del número verificado manda: la sesión pasa a esta cuenta.
            previous_chat_id = store.telegram_chat_id
        store.telegram_chat_id = chat_id
        store.consent_at = await get_consent(session, store.id, chat_id)
        store.last_activity_at = datetime.now(UTC)
        store.idle_notice_sent_at = None
        wanted_signup = _start_choice.pop(chat_id, None) == "signup"
        _verified_phone.pop(chat_id, None)
        await _reply(update, texts.PHONE_VERIFIED, reply_markup=keyboards.remove_keyboard())
        if wanted_signup:
            # Quería registrarse, pero su número ya es de una tienda: el número manda y entra con ella.
            await _reply(update, texts.phone_has_store(store.name))
        if store.consent_at is None:
            await _reply(update, texts.CONSENT_NOTICE, reply_markup=keyboards.consent_keyboard())
        else:
            # Esta misma cuenta ya había aceptado para esta tienda: entra directo.
            await _reply(update, texts.welcome_back(store.owner_name, store.name))
            if store.distributor_id is None:
                await _reply(update, texts.NO_SUPPLIERS)
            elif store.delivery_days_set_at is None:
                await ask_days(update, store)
    if previous_chat_id is not None and context is not None:
        try:
            await context.bot.send_message(previous_chat_id, texts.SESSION_MOVED)
        except Exception:
            logger.info("No se pudo avisar al chat anterior que se cerró la sesión")


async def handle_start_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Botones de /start: 🔑 ya tengo cuenta o 🆕 registrar mi tienda."""
    query = update.callback_query
    chat_id = _chat_id(update)
    if query is None or chat_id is None:
        return
    await query.answer()
    await _remove_buttons(update)
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is not None:
            await _reply(update, texts.already_logged_in(store.name))
            return
        signup = await registration.get_registration(session, chat_id)
        if signup is not None:
            await registration.ask(signup, _say(update))
            return
        if query.data == keyboards.START_LOGIN:
            _start_choice[chat_id] = "login"
            await _ask_login(update)
            return
        _start_choice[chat_id] = "signup"
        phone = _verified_phone.pop(chat_id, None)
        if phone is None:
            await _ask_login(update, texts.ASK_SIGNUP_PHONE)
            return
        # Ya había compartido su número (que no es de ninguna tienda): empieza el registro de una vez.
        if await session.scalar(select(Store.id).where(Store.owner_phone == phone)) is not None:
            await _ask_login(update)
            return
        await registration.begin(session, chat_id, phone, _say(update))


async def get_consent(session: AsyncSession, store_id: int, chat_id: int) -> datetime | None:
    return await session.scalar(
        select(StoreConsent.accepted_at).where(
            StoreConsent.store_id == store_id, StoreConsent.telegram_chat_id == chat_id
        )
    )


async def handle_consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None:
            signup = await registration.get_registration(session, chat_id)
            if signup is not None:
                await registration.accept_consent(signup, _say(update))
            else:
                await _ask_login(update)
            await _remove_buttons(update)
            return
        if store.consent_at is None:
            store.consent_at = datetime.now(UTC)
            if await get_consent(session, store.id, chat_id) is None:
                session.add(StoreConsent(store_id=store.id, telegram_chat_id=chat_id, accepted_at=store.consent_at))
        await _reply(update, texts.WELCOME.format(owner=store.owner_name))
        if store.distributor_id is None:
            await _reply(update, texts.NO_SUPPLIERS)
        elif store.delivery_days_set_at is None:
            await ask_days(update, store)
    await _remove_buttons(update)


async def handle_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Marca/desmarca días de entrega y, con "Listo", los guarda (solo la primera vez)."""
    query = update.callback_query
    chat_id = _chat_id(update)
    if query is None or chat_id is None or not query.data:
        return
    selected, action = keyboards.parse_days_callback(query.data)
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None or store.consent_at is None:
            await query.answer()
            await _ask_login(update)
            return
        if store.delivery_days_set_at is not None:
            # Botón viejo: los días ya quedaron escogidos.
            await query.answer()
            await _remove_buttons(update)
            return
        allowed = route_days(store)
        selected = [d for d in selected if d in allowed]
        if action != "ok":
            day = int(action)
            selected = sorted(set(selected) ^ {day}) if day in allowed else selected
            await query.answer()
            try:
                await query.edit_message_reply_markup(reply_markup=keyboards.days_keyboard(allowed, selected))
            except Exception:
                logger.info("No se pudieron actualizar los botones de días")
            return
        if not selected:
            await query.answer(texts.DAYS_EMPTY, show_alert=True)
            return
        await query.answer()
        store.visit_weekdays = ",".join(str(d) for d in selected)
        store.delivery_days_set_at = datetime.now(UTC)
        await mirror_store_link(session, store)
    await _remove_buttons(update)
    await _reply(update, texts.days_saved(selected))


async def handle_supplier_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Días de entrega con un proveedor que acaba de aceptar (botones del aviso de aceptación)."""
    query = update.callback_query
    chat_id = _chat_id(update)
    if query is None or chat_id is None or not query.data:
        return
    link_id, selected, action = keyboards.parse_supplier_days_callback(query.data)
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        link = await session.get(StoreSupplier, link_id) if store is not None else None
        if store is None or link is None or link.store_id != store.id or link.status != SUPPLIER_APPROVED:
            await query.answer()
            await _stale_button(update)
            return
        if link.delivery_days_set_at is not None:
            await query.answer()  # botón viejo: los días con este proveedor ya quedaron escogidos
            await _remove_buttons(update)
            return
        allowed = parse_visit_weekdays(link.route_weekdays or "") or list(range(7))
        selected = [day for day in selected if day in allowed]
        if action != "ok":
            day = int(action)
            selected = sorted(set(selected) ^ {day}) if day in allowed else selected
            await query.answer()
            try:
                await query.edit_message_reply_markup(
                    reply_markup=keyboards.supplier_days_keyboard(link.id, allowed, selected)
                )
            except Exception:
                logger.info("No se pudieron actualizar los botones de días del proveedor")
            return
        if not selected:
            await query.answer(texts.DAYS_EMPTY, show_alert=True)
            return
        await query.answer()
        set_supplier_days(link, store, selected)
        distributor = await session.scalar(select(Distributor.name).where(Distributor.id == link.distributor_id))
        first = len(await approved_suppliers(session, store.id)) == 1
    await _remove_buttons(update)
    await _reply(update, texts.supplier_days_saved(distributor or "Tu proveedor", selected, first))


async def handle_reject_consent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """No acepta: se desvincula el chat y vuelve al paso de compartir el número."""
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None:
            signup = await registration.get_registration(session, chat_id)
            if signup is not None:
                await registration.reject_consent(session, signup, _say(update))
            else:
                await _ask_login(update)
            await _remove_buttons(update)
            return
        if store.consent_at is not None:
            # Botón viejo de un aviso ya aceptado: no se desvincula por accidente.
            await _reply(update, texts.CONSENT_ALREADY)
            return
        store.telegram_chat_id = None
    await _remove_buttons(update)
    await _ask_login(update, texts.CONSENT_REJECTED)


async def handle_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None:
            await _ask_login(update)
            return
        name = store.name
    await _reply(update, texts.logout_question(name), reply_markup=keyboards.logout_keyboard())


async def handle_confirm_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Desvincula el chat. La autorización de datos queda guardada para esta cuenta (StoreConsent)."""
    await _answer_callback(update)
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    async with session_scope() as session:
        store = await get_store_by_chat(session, chat_id)
        if store is None:
            await _ask_login(update)
            return
        pending = (await session.scalars(select(PendingAction).where(PendingAction.store_id == store.id))).all()
        for row in pending:
            await session.delete(row)
        store.telegram_chat_id = None
        store.consent_at = None
        store.last_activity_at = None
        store.idle_notice_sent_at = None
    await _remove_buttons(update)
    await _ask_login(update, texts.LOGGED_OUT)


async def handle_keep_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # La actividad ya quedó registrada en require_link: solo se confirma.
    await _answer_callback(update)
    await _remove_buttons(update)
    await _reply(update, texts.SESSION_KEPT)


async def _remove_buttons(update: Update) -> None:
    """Quita los botones del aviso para que no se pulsen dos veces."""
    query = update.callback_query
    if query is None:
        return
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        logger.info("No se pudieron quitar los botones del aviso")


KIND_SALES = "sales"
KIND_STOCK = "stock"


async def load_active_products(session: AsyncSession, distributor_id: int) -> list[Product]:
    result = await session.scalars(
        select(Product).where(Product.distributor_id == distributor_id, Product.active.is_(True))
    )
    return list(result.all())


def sku_keys(products: list[Product]) -> dict[int, str]:
    """Código de cada producto en el catálogo de la tienda. Si dos proveedores usan el mismo código
    para productos distintos, se les agrega "~<proveedor>" para no confundirlos."""
    counts = Counter(product.sku for product in products)
    return {
        product.id: product.sku if counts[product.sku] == 1 else f"{product.sku}~{product.distributor_id}"
        for product in products
    }


def products_as_catalog(products: list[Product]) -> list[dict]:
    keys = sku_keys(products)
    return [
        {
            "sku": keys[product.id],
            "name": product.name,
            "aliases": product.aliases,
            "presentation": product.presentation,
        }
        for product in products
    ]


def names_by_sku(products: list[Product]) -> dict[str, str]:
    keys = sku_keys(products)
    return {keys[product.id]: product.name for product in products}


def products_by_sku(products: list[Product]) -> dict[str, Product]:
    keys = sku_keys(products)
    return {keys[product.id]: product for product in products}


def parse_stock_correction(text: str) -> tuple[str, int] | None:
    parts = text.strip().split()
    if len(parts) < 2 or not parts[-1].isdigit():
        return None
    name = " ".join(parts[:-1]).strip()
    if not name:
        return None
    return name, int(parts[-1])


def apply_stock_correction(lines: list[dict], sku: str, quantity: int) -> list[dict]:
    updated = False
    result: list[dict] = []
    for line in lines:
        if line.get("sku") == sku:
            result.append({**line, "sku": sku, "quantity": quantity})
            updated = True
        else:
            result.append(line)
    if not updated:
        result.append({"sku": sku, "quantity": quantity, "confidence": 1.0})
    return result


def _kind_filter(kind: str):
    """Los pedidos van uno por proveedor ("order:<id>"): "order" a secas es cualquiera de ellos."""
    if kind == KIND_ORDER:
        return or_(PendingAction.kind == KIND_ORDER, PendingAction.kind.like(f"{KIND_ORDER}:%"))
    return PendingAction.kind == kind


async def get_pending(session: AsyncSession, store_id: int, kind: str) -> PendingAction | None:
    now = datetime.now(UTC)
    return await session.scalar(
        select(PendingAction)
        .where(
            PendingAction.store_id == store_id,
            _kind_filter(kind),
            PendingAction.expires_at > now,
        )
        .order_by(PendingAction.id.desc())
    )


async def replace_pending(
    session: AsyncSession, store_id: int, kind: str, payload: dict, ttl: timedelta = PENDING_TTL
) -> PendingAction:
    """Un borrador por tipo: el nuevo reemplaza al anterior del mismo tipo.

    Así registrar una venta no borra el pedido que el tendero está armando. Cada cambio crea
    un borrador con id nuevo: los botones de los mensajes anteriores dejan de servir.
    """
    rows = (
        await session.scalars(
            select(PendingAction).where(PendingAction.store_id == store_id, PendingAction.kind == kind)
        )
    ).all()
    for row in rows:
        await session.delete(row)
    pending = PendingAction(
        store_id=store_id,
        kind=kind,
        payload=payload,
        expires_at=datetime.now(UTC) + ttl,
    )
    session.add(pending)
    await session.flush()
    return pending


async def get_button_draft(update: Update, session: AsyncSession, store_id: int, kind: str) -> PendingAction | None:
    """El borrador del mensaje cuyo botón se pulsó, si sigue vigente.

    Un botón de un resumen anterior (ya reemplazado, confirmado o vencido) no devuelve nada,
    para que nunca confirme algo distinto de lo que el tendero tiene en pantalla.
    FOR UPDATE: con dos toques seguidos, el segundo espera y ya no encuentra el borrador.
    """
    query = update.callback_query
    pending_id = keyboards.draft_id(query.data if query is not None else None)
    if pending_id is None:
        return None
    return await session.scalar(
        select(PendingAction).where(
            PendingAction.id == pending_id,
            PendingAction.store_id == store_id,
            _kind_filter(kind),
            PendingAction.expires_at > datetime.now(UTC),
        )
        .with_for_update()
    )


async def _stale_button(update: Update) -> None:
    await _remove_buttons(update)
    await _reply(update, texts.STALE_BUTTON)


async def order_sent_for(
    session: AsyncSession, store_id: int, delivery: date, distributor_id: int | None = None
) -> bool:
    """¿Ya hay un pedido enviado (o recibido) para esa entrega? Con proveedor: a ese proveedor."""
    query = select(SuggestedOrder.id).where(
        SuggestedOrder.store_id == store_id,
        SuggestedOrder.delivery_date == delivery,
        SuggestedOrder.status.in_(("sent", "received")),
    )
    if distributor_id is not None:
        query = query.where(SuggestedOrder.distributor_id == distributor_id)
    return await session.scalar(query) is not None


async def _download_file(context: ContextTypes.DEFAULT_TYPE, file_id: str) -> bytes:
    tg_file = await context.bot.get_file(file_id)
    return bytes(await tg_file.download_as_bytearray())


async def _answer_callback(update: Update) -> None:
    query = update.callback_query
    if query is not None:
        await query.answer()


async def save_and_show_sales(
    update: Update,
    store_id: int,
    lines: list[SaleLine],
    source: str,
    names: dict[str, str],
) -> None:
    payload_lines = [line.model_dump() for line in lines]
    summary = texts.sales_summary(payload_lines, names)
    if not payload_lines:
        await _reply(update, summary)
        return
    async with session_scope() as session:
        pending = await replace_pending(
            session,
            store_id,
            KIND_SALES,
            {"source": source, "lines": payload_lines},
        )
        pending_id = pending.id
    await _reply(update, summary, reply_markup=keyboards.confirm_sales_keyboard(pending_id))


async def save_and_show_stock(
    update: Update,
    store_id: int,
    lines: list[StockLine],
    names: dict[str, str],
) -> None:
    payload_lines = [line.model_dump() for line in lines]
    summary = texts.stock_summary(payload_lines, names)
    if not payload_lines:
        await _reply(update, summary)
        return
    async with session_scope() as session:
        pending = await replace_pending(
            session,
            store_id,
            KIND_STOCK,
            {"source": "photo", "lines": payload_lines},
        )
        pending_id = pending.id
    await _reply(update, summary, reply_markup=keyboards.confirm_stock_keyboard(pending_id))


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.voice is None:
        return
    voice = message.voice
    if voice.duration > MAX_VOICE_SECONDS:
        await _reply(update, texts.VOICE_TOO_LONG)
        return
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        store_id = store.id
    audio_bytes = await _download_file(context, voice.file_id)
    async with logged_task("voz"):
        set_task_info(store_id=store_id)
        try:
            transcript = await transcribe_audio(audio_bytes, voice.mime_type or "audio/ogg")
        except Exception:
            logger.warning("No se pudo transcribir el audio")
            transcript = ""
        if not transcript:
            set_task_info(outcome="no_entendido")
            await _reply(update, texts.AI_FAIL)
            return
        await route_text(update, transcript, "voice")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.photo:
        return
    photo = message.photo[-1]
    if photo.file_size is not None and photo.file_size > MAX_PHOTO_BYTES:
        await _reply(update, texts.PHOTO_TOO_BIG)
        return
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        store_id = store.id
        products = await store_products(session, store)
        catalog = products_as_catalog(products)
        names = names_by_sku(products)
    image_bytes = await _download_file(context, photo.file_id)
    if len(image_bytes) > MAX_PHOTO_BYTES:
        await _reply(update, texts.PHOTO_TOO_BIG)
        return
    note = (message.caption or "").strip()
    async with logged_task("foto"):
        set_task_info(store_id=store_id)
        try:
            lines = await extract_stock(catalog, image_bytes, "image/jpeg", note=note)
        except ExtractError as exc:
            set_task_info(outcome="no_entendido")
            await _reply(update, exc.user_message)
            return
        lines = match_stock_lines(lines, catalog)
        set_task_info(outcome="conteo" if lines else "sin_resultado")
        await save_and_show_stock(update, store_id, lines, names)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    text = (message.text or "").strip()
    if not text:
        return
    async with logged_task("texto"):
        await route_text(update, text, "text")
    raise ApplicationHandlerStop


async def route_text(update: Update, text: str, source: str) -> None:
    """Texto del tendero (escrito o de un audio): corrige el conteo o lo recibido, cambia el pedido
    o registra ventas."""
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        store_id = store.id
        set_task_info(store_id=store_id)
        products = await store_products(session, store)
        catalog = products_as_catalog(products)
        names = names_by_sku(products)
        correction = parse_stock_correction(text)
        if correction is not None:
            # "coca litro 8" corrige el último resumen abierto: el conteo de la foto o lo recibido.
            drafts = [
                draft
                for draft in (
                    await get_pending(session, store.id, KIND_STOCK),
                    await get_pending(session, store.id, KIND_RECEIPT),
                )
                if draft is not None
            ]
            if drafts:
                draft = max(drafts, key=lambda row: row.id)
                if draft.kind == KIND_RECEIPT:
                    await apply_receipt_correction(update, session, store, draft, correction)
                    return
                name, quantity = correction
                match = best_product_match(name, catalog)
                if match is None:
                    await _reply(update, texts.AI_FAIL)
                    return
                sku = str(match["sku"])
                lines = apply_stock_correction(list(draft.payload.get("lines") or []), sku, quantity)
                pending = await replace_pending(session, store.id, KIND_STOCK, {**draft.payload, "lines": lines})
                await _reply(
                    update,
                    texts.stock_summary(lines, names),
                    reply_markup=keyboards.confirm_stock_keyboard(pending.id),
                )
                return
        pending_order = await get_pending(session, store.id, KIND_ORDER)
        order_payload = dict(pending_order.payload) if pending_order is not None else None
        distributor_id = store.distributor_id

    if get_settings().agent_enabled and await answer_with_agent(update, store_id, distributor_id, text, source, products):
        return

    # Respaldo sin agente: extracción directa (si el agente está apagado o Grok falló antes de responder).
    set_task_info(outcome="respaldo")
    if order_payload is not None and await apply_order_message(update, store_id, text, products, order_payload):
        set_task_info(outcome="respaldo_pedido")
        return

    # Todo texto va a Grok: el tendero no tiene que decir "vendí" para registrar una venta.
    try:
        lines = await extract_sales(catalog, transcript=text)
    except ExtractError as exc:
        await _reply(update, exc.user_message)
        return
    lines = match_sale_lines(lines, catalog)
    set_task_info(outcome="respaldo_ventas" if lines else "respaldo")
    await save_and_show_sales(update, store_id, lines, source, names)


async def apply_order_message(
    update: Update, store_id: int, text: str, products: list[Product], payload: dict
) -> bool:
    """Si el mensaje pide cambios al pedido abierto, los aplica y muestra el pedido nuevo.

    Devuelve False si el mensaje no era sobre el pedido (entonces se trata como ventas).
    """
    catalog = products_as_catalog(products)
    by_id = {product.id: product for product in products}
    current = to_order_lines(list(payload.get("lines") or []), by_id)
    current_text = "\n".join(f"- sku={line.sku} | {line.name} | {line.cases} cajas" for line in current) or "(vacío)"
    try:
        changes = await extract_order_changes(catalog, text, current_text)
    except ExtractError:
        return False
    if not changes:
        return False
    changes = match_order_changes(changes, catalog)
    messages, _info = await change_open_order(store_id, changes, products)
    for message in messages:
        await _reply(update, message.text, reply_markup=message.markup)
    return True


@dataclass
class BotMessage:
    text: str
    markup: object | None = None


async def supplier_name(session: AsyncSession, distributor_id: int | None) -> str | None:
    if distributor_id is None:
        return None
    distributor = await session.get(Distributor, distributor_id)
    return distributor.name if isinstance(distributor, Distributor) else None


async def supplier_schedule(session: AsyncSession, store: Store, distributor_id: int | None):
    """Próxima entrega y hora límite con ese proveedor (sus días de entrega y su anticipación)."""
    link = None
    if distributor_id is not None:
        link = await session.scalar(
            select(StoreSupplier).where(
                StoreSupplier.store_id == store.id,
                StoreSupplier.distributor_id == distributor_id,
                StoreSupplier.status == SUPPLIER_APPROVED,
            )
        )
    if link is not None:
        days, lead = link.visit_weekdays or link.route_weekdays or store.visit_weekdays, link.lead_time_days
    else:
        days, lead = store.visit_weekdays, store.lead_time_days
    return delivery_schedule(days, lead, datetime.now(UTC))


def order_kind(distributor_id: int | None) -> str:
    """Un borrador de pedido por proveedor: "order:<proveedor>"."""
    return f"{KIND_ORDER}:{distributor_id}" if distributor_id is not None else KIND_ORDER


async def change_open_order(
    store_id: int, changes: list[OrderChange], products: list[Product]
) -> tuple[list[BotMessage], dict]:
    """Aplica cambios a los pedidos abiertos: cada producto va al pedido de su proveedor.

    Devuelve los mensajes con botones (uno por pedido cambiado) y un resumen para el agente.
    """
    by_sku = products_by_sku(products)
    groups: dict[int | None, list[OrderChange]] = defaultdict(list)
    unknown: list[str] = []
    for change in changes:
        product = by_sku.get(change.sku or "")
        if product is None:
            unknown.append(change.product_text or "producto")
        else:
            groups[getattr(product, "distributor_id", None)].append(change)
    messages: list[BotMessage] = []
    updated: list[dict] = []
    closed: list[str] = []
    async with session_scope() as session:
        for distributor_id, group in groups.items():
            name = await supplier_name(session, distributor_id)
            pending = await get_pending(session, store_id, order_kind(distributor_id))
            if pending is None:
                closed.append(name or "tu proveedor")
                continue
            payload = dict(pending.payload)
            supplier_products = {
                p.id: p for p in products if getattr(p, "distributor_id", None) == distributor_id
            } or {p.id: p for p in products}
            result = apply_order_changes(list(payload.get("lines") or []), group, by_sku)
            capped = cap_to_available(result.lines, supplier_products)
            new = await replace_pending(
                session, store_id, pending.kind, {**payload, "distributor_id": distributor_id, "lines": capped.lines},
                ttl=ORDER_PENDING_TTL,
            )
            lines = to_order_lines(capped.lines, supplier_products)
            total = order_total(lines)
            messages.append(BotMessage(
                texts.order_draft(
                    lines, total, date.fromisoformat(payload["delivery_date"]),
                    datetime.fromisoformat(payload["order_deadline"]), result.not_found, capped.not_found,
                    supplier=name,
                ),
                keyboards.order_keyboard(new.id),
            ))
            updated.append({"proveedor": name, "productos": len(lines), "total_pesos": int(total),
                            "recortados_por_disponibilidad": capped.not_found})
    if not updated:
        if closed:
            return [BotMessage(texts.NOTHING_PENDING)], {
                "error": f"No hay un pedido abierto con {', '.join(closed)}. Arma uno primero con armar_pedido."
            }
        return [], {"error": "No reconocí los productos del cambio.", "no_encontrados": unknown}
    info = {"borrador": "actualizado", "pedidos": updated, "no_encontrados": unknown,
            "sin_pedido_abierto": closed, "nota": "El tendero lo envía con el botón de cada pedido."}
    if len(updated) == 1:
        info.update(updated[0])
    return messages, info


async def prepare_orders(session: AsyncSession, store) -> list[tuple[BotMessage, dict]] | None:
    """Un pedido sugerido por proveedor para su próxima entrega, cada uno como borrador. None si no hay tienda."""
    analysis = await load_store_analysis(session, store.id)
    if analysis is None:
        return None
    results = []
    for plan in analysis.plans:
        distributor_id = plan.distributor_id
        name = await supplier_name(session, distributor_id)
        if await order_sent_for(session, store.id, plan.next_delivery, distributor_id):
            results.append((BotMessage(texts.order_already_sent(plan.next_delivery, name)),
                            {"proveedor": name, "ya_enviado": plan.next_delivery.isoformat()}))
            continue
        target = distributor_id if distributor_id is not None else store.distributor_id
        by_id = {p.id: p for p in await load_active_products(session, target)}
        capped = cap_to_available(
            [{"product_id": line.product_id, "cases": line.cases, "reason": line.reason} for line in plan.order_lines],
            by_id,
        )
        pending = await replace_pending(
            session,
            store.id,
            order_kind(distributor_id),
            {
                "distributor_id": distributor_id,
                "lines": capped.lines,
                "delivery_date": plan.next_delivery.isoformat(),
                "order_deadline": plan.order_deadline.isoformat(),
            },
            ttl=ORDER_PENDING_TTL,
        )
        lines = to_order_lines(capped.lines, by_id)
        total = order_total(lines)
        message = BotMessage(
            texts.order_draft(lines, total, plan.next_delivery, plan.order_deadline,
                              availability_notes=capped.not_found, supplier=name),
            keyboards.order_keyboard(pending.id),
        )
        results.append((message, {
            "proveedor": name,
            "borrador": "creado",
            "entrega": texts.day_label(plan.next_delivery),
            "hora_limite": texts.deadline_label(plan.order_deadline),
            "productos": len(lines),
            "total_pesos": int(total),
            "nota": "El tendero lo revisa y lo envía con el botón del pedido.",
        }))
    return results


def agent_catalog(products: list[Product]) -> list[dict]:
    return [
        {
            **item,
            "unit_price": int(product.unit_price),
            "units_per_case": product.units_per_case,
            # Agotado = no alcanza para una caja completa (igual que en /catalogo).
            "sold_out": product.available_units is not None
            and product.available_units < max(1, product.units_per_case),
        }
        for item, product in zip(products_as_catalog(products), products, strict=True)
    ]


def _days_left(value: float) -> float | None:
    return round(value, 1) if math.isfinite(value) else None


def store_actions(store_id: int, products: list[Product], source: str, shown: dict[str, BotMessage]) -> StoreActions:
    """Las herramientas del agente para una tienda. Lo que hay que mostrar con botones queda en `shown`
    (uno por tipo: si el agente arma dos borradores del mismo tipo, vale el último)."""
    names = names_by_sku(products)

    async def stock() -> list[dict]:
        async with session_scope() as session:
            analysis = await load_store_analysis(session, store_id)
        if analysis is None:
            return []
        return [
            {
                "sku": item.sku,
                "producto": item.name,
                "unidades": item.stock,
                "tiene_conteo": item.has_count,
                "dias_que_alcanza": _days_left(item.coverage_days),
            }
            for item in analysis.products
        ]

    async def alerts() -> dict:
        async with session_scope() as session:
            analysis = await load_store_analysis(session, store_id)
        if analysis is None:
            return {"en_riesgo": [], "estancados": []}
        return {
            "en_riesgo": [{"producto": p.name, "motivo": p.reason} for p in analysis.at_risk],
            "estancados": [{"producto": p.name, "motivo": p.reason} for p in analysis.stagnant],
            "proxima_entrega": texts.day_label(analysis.next_delivery),
        }

    async def draft_sales(lines: list[SaleLine]) -> dict:
        if not lines:
            return {"borrador": "no_creado", "motivo": "Ningún producto se reconoció"}
        payload_lines = [line.model_dump() for line in lines]
        async with session_scope() as session:
            pending = await replace_pending(session, store_id, KIND_SALES, {"source": source, "lines": payload_lines})
            pending_id = pending.id
        shown[KIND_SALES] = BotMessage(
            texts.sales_summary(payload_lines, names), keyboards.confirm_sales_keyboard(pending_id)
        )
        return {"borrador": "creado"}

    async def suggest_order() -> dict:
        async with session_scope() as session:
            store = await session.get(Store, store_id)
            prepared = await prepare_orders(session, store) if store is not None else None
        if not prepared:
            return {"error": "No se pudo armar el pedido"}
        for index, (message, _info) in enumerate(prepared):
            shown[f"{KIND_ORDER}#{index}"] = message
        infos = [info for _message, info in prepared]
        return infos[0] if len(infos) == 1 else {"pedidos": infos, "nota": "Un pedido por proveedor."}

    async def change_order(changes: list[OrderChange]) -> dict:
        if not changes:
            return {"borrador": "sin_cambios"}
        messages, info = await change_open_order(store_id, changes, products)
        if "error" not in info:
            for index, message in enumerate(messages):
                shown[f"{KIND_ORDER}#{index}"] = message
        return info

    async def compare(text: str) -> dict:
        async with session_scope() as session:
            items = await load_items(session, await session.get(Store, store_id))
        if not text:
            rows = catalog_merge.savings(items)
            return {"comparacion": rows} if rows else {"nota": "Ningún producto lo venden varios de tus proveedores."}
        item = catalog_merge.find_by_text(items, text)
        if item is None:
            return {"no_encontrado": text}
        return catalog_merge.comparison(item)

    async def prefer(product_text: str, provider_text: str) -> dict:
        async with session_scope() as session:
            store = await session.get(Store, store_id)
            items = await load_items(session, store)
            item = catalog_merge.find_by_text(items, product_text)
            if item is None:
                return {"error": f"No encontré '{product_text}' entre los productos de tus proveedores."}
            if len(item.offers) < 2:
                return {"error": f"{item.name} solo lo vende {item.offers[0].distributor_name}."}
            offer = catalog_merge.find_provider(item, provider_text)
            if offer is None:
                return {"error": f"Ese proveedor no vende {item.name}.",
                        "proveedores": [o.distributor_name for o in item.offers]}
            await choose(session, store, items, offer.product.id)
        return {"producto": item.name, "ahora_le_pides_a": offer.distributor_name,
                "precio_unidad": int(offer.price)}

    return StoreActions(
        catalog=agent_catalog(products),
        stock=stock,
        alerts=alerts,
        draft_sales=draft_sales,
        suggest_order=suggest_order,
        change_order=change_order,
        compare=compare,
        prefer=prefer,
    )


def agent_outcome(result: AgentResult | None, shown: dict[str, BotMessage]) -> str:
    """Qué terminó haciendo el agente, para la bitácora de consumo."""
    if KIND_SALES in shown:
        return "ventas"
    if any(key.startswith(KIND_ORDER) for key in shown):
        return "pedido"
    if result is None:
        return "error"
    if not result.finished:
        return "sin_terminar"
    return "consulta" if result.tool_calls else "conversacion"


async def answer_with_agent(
    update: Update, store_id: int, distributor_id: int, text: str, source: str, products: list[Product]
) -> bool:
    """El agente con herramientas responde al tendero. False si no alcanzó a hacer nada (usar respaldo)."""
    shown: dict[str, BotMessage] = {}
    actions = store_actions(store_id, products, source, shown)
    try:
        result: AgentResult | None = await run_agent(text, build_tools(actions, user_message=text), AGENT_SYSTEM_PROMPT)
    except Exception:
        logger.warning("El agente falló (Grok no respondió)")
        result = None
        if not shown:
            return False
    set_task_info(outcome=agent_outcome(result, shown))
    reply = result.reply if result is not None else ""
    if reply:
        await _reply(update, reply)
    for message in shown.values():
        await _reply(update, message.text, reply_markup=message.markup)
    if not reply and not shown:
        await _reply(update, texts.AI_FAIL)
    return True


async def handle_confirm_sales(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_button_draft(update, session, store.id, KIND_SALES)
        if pending is None:
            await _stale_button(update)
            return
        products = products_by_sku(await store_products(session, store))
        source = pending.payload.get("source") or "text"
        if source not in {"voice", "text", "manual"}:
            source = "text"
        now = datetime.now(UTC)
        saved = 0
        for line in pending.payload.get("lines") or []:
            product = products.get(line.get("sku") or "")
            quantity = int(line.get("quantity") or 0)
            if product is None or quantity <= 0:
                continue
            session.add(
                Sale(
                    store_id=store.id,
                    product_id=product.id,
                    quantity=quantity,
                    unit_price=product.unit_price,
                    sold_at=now,
                    source=source,
                )
            )
            saved += 1
        await session.delete(pending)
    await _remove_buttons(update)
    await _reply(update, texts.SAVED_SALES if saved else texts.EMPTY_SALES)


async def handle_cancel_sales(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_button_draft(update, session, store.id, KIND_SALES)
        if pending is None:
            await _stale_button(update)
            return
        await session.delete(pending)
    await _remove_buttons(update)
    await _reply(update, texts.CANCELLED)


async def handle_confirm_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_button_draft(update, session, store.id, KIND_STOCK)
        if pending is None:
            await _stale_button(update)
            return
        products = products_by_sku(await store_products(session, store))
        source = pending.payload.get("source") or "photo"
        if source not in {"photo", "manual"}:
            source = "photo"
        now = datetime.now(UTC)
        saved = 0
        for line in pending.payload.get("lines") or []:
            product = products.get(line.get("sku") or "")
            quantity = int(line.get("quantity") or 0)
            if product is None or quantity < 0:
                continue
            session.add(
                StockCount(
                    store_id=store.id,
                    product_id=product.id,
                    quantity=quantity,
                    counted_at=now,
                    source=source,
                )
            )
            saved += 1
        await session.delete(pending)
    await _remove_buttons(update)
    await _reply(update, texts.SAVED_STOCK if saved else texts.EMPTY_STOCK)


async def handle_cancel_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_button_draft(update, session, store.id, KIND_STOCK)
        if pending is None:
            await _stale_button(update)
            return
        await session.delete(pending)
    await _remove_buttons(update)
    await _reply(update, texts.CANCELLED)


KIND_ORDER = "order"
KIND_RECEIPT = "receipt"


async def handle_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        analysis = await load_store_analysis(session, store.id)
    if analysis is None or not any(p.has_count for p in analysis.products):
        await _reply(update, texts.NO_STOCK)
        return
    products = sorted(analysis.products, key=lambda p: (not p.has_count, p.name))
    await _reply(update, texts.stock_list(products))


async def handle_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        analysis = await load_store_analysis(session, store.id)
    if analysis is None:
        await _ask_login(update)
        return
    await _reply(update, texts.alerts_list(analysis.at_risk, analysis.stagnant))


async def handle_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pedido sugerido para la próxima entrega. Queda como borrador que el tendero puede cambiar."""
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        prepared = await prepare_orders(session, store)
    if prepared is None:
        await _ask_login(update)
        return
    for message, _info in prepared:
        await _reply(update, message.text, reply_markup=message.markup)


async def handle_send_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_button_draft(update, session, store.id, KIND_ORDER)
        if pending is None:
            await _stale_button(update)
            return
        distributor_id = pending.payload.get("distributor_id") or store.distributor_id
        name = await supplier_name(session, distributor_id)
        # Si pasó la hora límite mientras lo revisaba, el pedido va para la entrega siguiente de ese proveedor.
        schedule = await supplier_schedule(session, store, distributor_id)
        if await order_sent_for(session, store.id, schedule.next_delivery, distributor_id):
            # Un solo pedido por entrega y proveedor (p. ej. dos borradores abiertos o doble toque).
            await session.delete(pending)
            await _remove_buttons(update)
            await _reply(update, texts.order_already_sent(schedule.next_delivery, name))
            return
        draft_lines = list(pending.payload.get("lines") or [])
        product_ids = [int(line.get("product_id") or 0) for line in draft_lines]
        # FOR UPDATE: dos tiendas no pueden llevarse la misma última caja.
        locked = (
            await session.scalars(
                select(Product)
                .where(
                    Product.id.in_(product_ids),
                    Product.distributor_id == distributor_id,
                    Product.active.is_(True),
                )
                .with_for_update()
            )
        ).all()
        products = {p.id: p for p in locked}
        capped = cap_to_available(draft_lines, products)
        lines = to_order_lines(capped.lines, products)
        drafted_for = pending.payload.get("delivery_date")
        await session.delete(pending)
        if not lines:
            await _remove_buttons(update)
            await _reply(update, texts.NO_ORDER)
            return
        missed_deadline = drafted_for is not None and drafted_for != schedule.next_delivery.isoformat()
        order = SuggestedOrder(store_id=store.id, distributor_id=distributor_id, status="sent",
                               delivery_date=schedule.next_delivery)
        for line in lines:
            order.items.append(
                SuggestedOrderItem(
                    product_id=line.product_id,
                    cases=line.cases,
                    units=line.units,
                    reason=line.reason[:240],
                )
            )
            product = products[line.product_id]
            if product.available_units is not None:
                product.available_units -= line.units
        session.add(order)
    await _remove_buttons(update)
    await _reply(update, texts.order_sent(schedule.next_delivery, missed_deadline, capped.not_found, supplier=name))


async def handle_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_button_draft(update, session, store.id, KIND_ORDER)
        if pending is None:
            await _stale_button(update)
            return
        await session.delete(pending)
    await _remove_buttons(update)
    await _reply(update, texts.ORDER_CANCELLED)


async def oldest_sent_order(session: AsyncSession, store_id: int, exclude_id: int | None = None):
    """El pedido enviado que debería llegar primero (el de entrega más cercana)."""
    query = select(SuggestedOrder).where(SuggestedOrder.store_id == store_id, SuggestedOrder.status == "sent")
    if exclude_id is not None:
        query = query.where(SuggestedOrder.id != exclude_id)
    return await session.scalar(
        query.order_by(
            SuggestedOrder.delivery_date.asc().nulls_last(), SuggestedOrder.created_at, SuggestedOrder.id
        )
    )


async def handle_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Muestra lo que trae el pedido para que el tendero confirme o corrija lo que llegó."""
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        order = await oldest_sent_order(session, store.id)
        if order is None:
            await _reply(update, texts.NO_SENT_ORDER)
            return
        rows = (
            await session.execute(
                select(SuggestedOrderItem, Product)
                .join(Product, Product.id == SuggestedOrderItem.product_id)
                .where(SuggestedOrderItem.order_id == order.id, SuggestedOrderItem.units > 0)
                .order_by(Product.name)
            )
        ).all()
        lines = [
            {"product_id": product.id, "name": product.name, "units": item.units, "ordered": item.units}
            for item, product in rows
        ]
        delivery = order.delivery_date
        supplier = await supplier_name(session, getattr(order, "distributor_id", None))
        pending = await replace_pending(
            session,
            store.id,
            KIND_RECEIPT,
            {"order_id": order.id, "delivery_date": delivery.isoformat() if delivery else None, "lines": lines,
             "supplier": supplier},
        )
        pending_id = pending.id
    await _reply(
        update, texts.receipt_summary(lines, delivery, supplier),
        reply_markup=keyboards.confirm_receipt_keyboard(pending_id),
    )


async def apply_receipt_correction(
    update: Update, session: AsyncSession, store: Store, draft: PendingAction, correction: tuple[str, int]
) -> None:
    """Con "coca litro 8" el tendero dice que de ese producto llegaron 8 unidades.

    Solo se corrigen productos que venían en el pedido.
    """
    name, quantity = correction
    delivery = draft.payload.get("delivery_date")
    lines = [dict(line) for line in draft.payload.get("lines") or []]
    ids = [int(line["product_id"]) for line in lines]
    # Los productos de ESTE pedido (de cualquiera de los proveedores de la tienda).
    order_products = list((await session.scalars(select(Product).where(Product.id.in_(ids)))).all())
    match = best_product_match(name, products_as_catalog(order_products))
    if match is None:
        await _reply(update, texts.RECEIPT_NOT_IN_ORDER)
        return
    product_id = products_by_sku(order_products)[match["sku"]].id
    for line in lines:
        if int(line["product_id"]) == product_id:
            line["units"] = quantity
    pending = await replace_pending(session, store.id, KIND_RECEIPT, {**draft.payload, "lines": lines})
    await _reply(
        update,
        texts.receipt_summary(lines, date.fromisoformat(delivery) if delivery else None, draft.payload.get("supplier")),
        reply_markup=keyboards.confirm_receipt_keyboard(pending.id),
    )


async def handle_confirm_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Suma al inventario lo que de verdad llegó y da el pedido por recibido."""
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_button_draft(update, session, store.id, KIND_RECEIPT)
        order = await session.get(SuggestedOrder, int(pending.payload.get("order_id") or 0)) if pending else None
        if pending is None or order is None or order.store_id != store.id or order.status != "sent":
            if pending is not None:
                await session.delete(pending)
            await _stale_button(update)
            return
        now = datetime.now(UTC)
        missing: list[str] = []
        for line in pending.payload.get("lines") or []:
            units, ordered = int(line.get("units") or 0), int(line.get("ordered") or 0)
            if units > 0:
                session.add(
                    Receipt(store_id=store.id, product_id=int(line["product_id"]), quantity=units, received_at=now)
                )
            if units < ordered:
                missing.append(f"{line['name']} ({ordered - units} de {ordered})")
        order.status = "received"
        await session.delete(pending)
        following = await oldest_sent_order(session, store.id, exclude_id=order.id)
        next_delivery = following.delivery_date if following is not None else None
    await _remove_buttons(update)
    await _reply(update, texts.receipt_saved(missing, next_delivery))


async def handle_cancel_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _answer_callback(update)
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        pending = await get_button_draft(update, session, store.id, KIND_RECEIPT)
        if pending is None:
            await _stale_button(update)
            return
        await session.delete(pending)
    await _remove_buttons(update)
    await _reply(update, texts.RECEIPT_CANCELLED)


async def handle_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with session_scope() as session:
        store = await require_ready_store(update, session)
        if store is None:
            return
        items = await load_items(session, store)
    for part in texts.store_catalog(items):
        await _reply(update, part)


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, texts.HELP)


async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, texts.UNKNOWN)


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Solo el tipo de error: nunca el contenido del mensaje ni datos del tendero.
    logger.error("Error procesando update: %s", type(context.error).__name__, exc_info=context.error)
    if isinstance(update, Update):
        try:
            await _reply(update, texts.AI_FAIL)
        except Exception:
            logger.warning("No se pudo avisar el error al chat")


def register_handlers(application: Application) -> None:
    text_only = filters.TEXT & ~filters.COMMAND

    # Grupo -2: rate limit antes que todo (lanza ApplicationHandlerStop si se pasa).
    application.add_handler(TypeHandler(Update, check_rate_limit), group=-2)

    # Grupo -1: sin tienda vinculada solo pasan /start y el número compartido.
    application.add_handler(TypeHandler(Update, require_link), group=-1)

    # Grupo 0: comandos, botones, voz, foto y número para entrar.
    application.add_handler(CommandHandler("start", handle_start))
    application.add_handler(CommandHandler("stock", handle_stock))
    application.add_handler(CommandHandler("alertas", handle_alerts))
    application.add_handler(CommandHandler("pedido", handle_order))
    application.add_handler(CommandHandler("recibido", handle_received))
    application.add_handler(CommandHandler("catalogo", handle_catalog))
    application.add_handler(CommandHandler("ayuda", handle_help))
    application.add_handler(CommandHandler("salir", handle_logout))
    application.add_handler(CommandHandler("cancelar", handle_cancel))
    application.add_handler(CommandHandler("proveedores", handle_providers))
    application.add_handler(MessageHandler(filters.COMMAND, handle_unknown))
    application.add_handler(CallbackQueryHandler(handle_start_choice, pattern=keyboards.START_PATTERN))
    application.add_handler(CallbackQueryHandler(handle_consent, pattern=f"^{keyboards.ACCEPT_CONSENT}$"))
    application.add_handler(
        CallbackQueryHandler(handle_reject_consent, pattern=f"^{keyboards.REJECT_CONSENT}$")
    )
    application.add_handler(CallbackQueryHandler(handle_days, pattern=keyboards.DAYS_PATTERN))
    application.add_handler(CallbackQueryHandler(handle_registration_button, pattern=keyboards.REG_PATTERN))
    application.add_handler(CallbackQueryHandler(handle_providers_button, pattern=keyboards.PROVIDERS_PATTERN))
    application.add_handler(CallbackQueryHandler(handle_end_supplier, pattern=keyboards.END_PATTERN))
    application.add_handler(CallbackQueryHandler(handle_supplier_days, pattern=keyboards.SUPPLIER_DAYS_PATTERN))
    application.add_handler(CallbackQueryHandler(handle_confirm_logout, pattern=f"^{keyboards.CONFIRM_LOGOUT}$"))
    application.add_handler(CallbackQueryHandler(handle_keep_session, pattern=f"^{keyboards.KEEP_SESSION}$"))
    application.add_handler(CallbackQueryHandler(handle_confirm_sales, pattern=keyboards.draft_pattern(keyboards.CONFIRM_SALES)))
    application.add_handler(CallbackQueryHandler(handle_cancel_sales, pattern=keyboards.draft_pattern(keyboards.CANCEL_SALES)))
    application.add_handler(CallbackQueryHandler(handle_confirm_stock, pattern=keyboards.draft_pattern(keyboards.CONFIRM_STOCK)))
    application.add_handler(CallbackQueryHandler(handle_cancel_stock, pattern=keyboards.draft_pattern(keyboards.CANCEL_STOCK)))
    application.add_handler(CallbackQueryHandler(handle_send_order, pattern=keyboards.draft_pattern(keyboards.SEND_ORDER)))
    application.add_handler(CallbackQueryHandler(handle_cancel_order, pattern=keyboards.draft_pattern(keyboards.CANCEL_ORDER)))
    application.add_handler(
        CallbackQueryHandler(handle_confirm_receipt, pattern=keyboards.draft_pattern(keyboards.CONFIRM_RECEIPT))
    )
    application.add_handler(
        CallbackQueryHandler(handle_cancel_receipt, pattern=keyboards.draft_pattern(keyboards.CANCEL_RECEIPT))
    )
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))

    # Grupo 1: texto de una tienda ya vinculada (ventas, cambios al pedido o correcciones).
    application.add_handler(MessageHandler(text_only, handle_text), group=1)

    # Grupo 2: si nadie lo entendió, respuesta amable con ejemplos.
    application.add_handler(MessageHandler(text_only, handle_unknown), group=2)

    application.add_error_handler(handle_error)
