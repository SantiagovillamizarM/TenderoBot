from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

ACCEPT_CONSENT = "consent:accept"
REJECT_CONSENT = "consent:reject"
CONFIRM_SALES = "sales:confirm"
CANCEL_SALES = "sales:cancel"
CONFIRM_STOCK = "stock:confirm"
CANCEL_STOCK = "stock:cancel"
SEND_ORDER = "order:send"
CANCEL_ORDER = "order:cancel"
CONFIRM_RECEIPT = "receipt:confirm"
CANCEL_RECEIPT = "receipt:cancel"
CONFIRM_LOGOUT = "logout:confirm"
KEEP_SESSION = "logout:keep"


def draft_pattern(action: str) -> str:
    """Botones de un borrador: "<acción>:<id del borrador>". Sin id = botón de antes de este cambio."""
    return rf"^{action}(:\d+)?$"


def draft_id(data: str | None) -> int | None:
    """'sales:confirm:42' -> 42. Un botón sin id no corresponde a ningún borrador vigente."""
    parts = (data or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return None
    return int(parts[2])


def _confirm_cancel(confirm_label: str, confirm: str, cancel: str, pending_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(confirm_label, callback_data=f"{confirm}:{pending_id}"),
                InlineKeyboardButton("❌ Cancelar", callback_data=f"{cancel}:{pending_id}"),
            ]
        ]
    )


def share_phone_keyboard() -> ReplyKeyboardMarkup:
    """Botón de Telegram que manda el número verificado de la cuenta (no se puede escribir otro)."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📱 Compartir mi número", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


START_LOGIN = "start:login"
START_SIGNUP = "start:signup"
START_PATTERN = r"^start:(login|signup)$"


def account_keyboard() -> InlineKeyboardMarkup:
    """Primera pregunta a un chat sin tienda: ¿ya tiene cuenta o va a registrar su tienda?"""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔑 Sí, ya tengo cuenta", callback_data=START_LOGIN)],
            [InlineKeyboardButton("🆕 No, quiero registrar mi tienda", callback_data=START_SIGNUP)],
        ]
    )


def register_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🆕 Registrar mi tienda", callback_data=START_SIGNUP)]])


def consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Acepto", callback_data=ACCEPT_CONSENT),
                InlineKeyboardButton("❌ No acepto", callback_data=REJECT_CONSENT),
            ]
        ]
    )


def confirm_sales_keyboard(pending_id: int) -> InlineKeyboardMarkup:
    return _confirm_cancel("✅ Confirmar", CONFIRM_SALES, CANCEL_SALES, pending_id)


def confirm_stock_keyboard(pending_id: int) -> InlineKeyboardMarkup:
    return _confirm_cancel("✅ Confirmar", CONFIRM_STOCK, CANCEL_STOCK, pending_id)


def confirm_receipt_keyboard(pending_id: int) -> InlineKeyboardMarkup:
    return _confirm_cancel("✅ Llegó así", CONFIRM_RECEIPT, CANCEL_RECEIPT, pending_id)


def logout_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🚪 Cerrar sesión", callback_data=CONFIRM_LOGOUT),
                InlineKeyboardButton("👍 Sigo aquí", callback_data=KEEP_SESSION),
            ]
        ]
    )


def order_keyboard(pending_id: int) -> InlineKeyboardMarkup:
    return _confirm_cancel("✅ Enviar al distribuidor", SEND_ORDER, CANCEL_ORDER, pending_id)


# Días de entrega: la selección viaja en el callback ("days:<días marcados>:<acción>"),
# así no hay estado a medio guardar. Acción = un día (0-6) para marcar/desmarcar u "ok".
DAYS_PREFIX = "days"
DAYS_PATTERN = r"^days:[0-6]*:([0-6]|ok)$"
WEEKDAY_NAMES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def days_keyboard(route_days: list[int], selected: list[int]) -> InlineKeyboardMarkup:
    marked = "".join(str(d) for d in sorted(selected))
    rows = [
        [
            InlineKeyboardButton(
                f"{'✅' if day in selected else '⬜'} {WEEKDAY_NAMES[day]}",
                callback_data=f"{DAYS_PREFIX}:{marked}:{day}",
            )
        ]
        for day in route_days
    ]
    rows.append([InlineKeyboardButton("Listo", callback_data=f"{DAYS_PREFIX}:{marked}:ok")])
    return InlineKeyboardMarkup(rows)


def parse_days_callback(data: str) -> tuple[list[int], str]:
    """'days:04:2' -> ([0, 4], '2')"""
    _prefix, marked, action = data.split(":")
    return sorted({int(d) for d in marked}), action


# --- Registro de una tienda nueva -------------------------------------------------

REG_PREFIX = "reg"
REG_PATTERN = r"^reg:(pay:(contado|credito)|confirm|restart)$"
REG_CONFIRM = "reg:confirm"
REG_RESTART = "reg:restart"
SKIP_LOCATION = "Saltar"


def payment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💵 De contado", callback_data="reg:pay:contado"),
                InlineKeyboardButton("📝 A crédito", callback_data="reg:pay:credito"),
            ]
        ]
    )


def location_keyboard() -> ReplyKeyboardMarkup:
    """Botón de Telegram que manda la ubicación del celular, o Saltar (es opcional)."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Enviar ubicación", request_location=True)], [KeyboardButton(SKIP_LOCATION)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def registration_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirmar", callback_data=REG_CONFIRM),
                InlineKeyboardButton("✏️ Empezar de nuevo", callback_data=REG_RESTART),
            ]
        ]
    )


# --- /proveedores ---------------------------------------------------------------------

PROVIDERS_PATTERN = r"^prov:[0-9,]*:(\d+|ok|list|done)$"


def _provider_data(selected: list[int], action: str) -> str:
    return f"prov:{','.join(str(i) for i in sorted(selected))}:{action}"


def providers_keyboard(options: list, selected: list[int]) -> InlineKeyboardMarkup | None:
    """Un botón por proveedor que se puede pedir (marcar/desmarcar) y "Enviar solicitudes"."""
    rows = [
        [InlineKeyboardButton(("✅ " if option.id in selected else "⬜ ") + option.name,
                              callback_data=_provider_data(selected, str(option.id)))]
        for option in options
    ]
    if not rows:
        return None
    label = f"📨 Enviar solicitudes ({len(selected)})" if selected else "📨 Enviar solicitudes"
    rows.append([InlineKeyboardButton(label, callback_data=_provider_data(selected, "ok"))])
    return InlineKeyboardMarkup(rows)


def providers_more_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("➕ Agregar otro", callback_data="prov::list"),
                InlineKeyboardButton("👍 Por ahora no", callback_data="prov::done"),
            ]
        ]
    )


def parse_providers_callback(data: str) -> tuple[list[int], str]:
    _, raw, action = data.split(":")
    return [int(part) for part in raw.split(",") if part.isdigit()], action


# --- Días de entrega con un proveedor (cada tienda puede tener varios) -----------------

SUPPLIER_DAYS_PATTERN = r"^sdays:\d+:[0-6]*:([0-6]|ok)$"


def supplier_days_keyboard(link_id: int, route_days: list[int], selected: list[int]) -> InlineKeyboardMarkup:
    marked = "".join(str(d) for d in sorted(selected))
    rows = [
        [
            InlineKeyboardButton(
                f"{'✅' if day in selected else '⬜'} {WEEKDAY_NAMES[day]}",
                callback_data=f"sdays:{link_id}:{marked}:{day}",
            )
        ]
        for day in route_days
    ]
    rows.append([InlineKeyboardButton("Listo", callback_data=f"sdays:{link_id}:{marked}:ok")])
    return InlineKeyboardMarkup(rows)


def parse_supplier_days_callback(data: str) -> tuple[int, list[int], str]:
    """'sdays:12:04:2' -> (12, [0, 4], '2')"""
    _prefix, link_id, marked, action = data.split(":")
    return int(link_id), sorted({int(d) for d in marked}), action
