"""Aviso por Telegram de lo que decidió un proveedor sobre la solicitud del tendero (StoreSupplier).

- Aceptó: "✅ X aceptó tu solicitud…" con botones para escoger los días de entrega con ese proveedor.
- Rechazó: "❌ X no aceptó tu solicitud. Motivo: …" y la invitación a escoger otros con /proveedores.

Se avisa apenas el proveedor decide (desde el panel, en segundo plano). Si falló, la tarea de fondo
lo reintenta (send_supplier_followups). decision_notified_at evita avisar dos veces.
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from telegram import Bot

from app.bot import keyboards, texts
from app.db import SessionLocal
from app.inventory.engine import parse_visit_weekdays
from app.models import SUPPLIER_APPROVED, SUPPLIER_PENDING, Distributor, Store, StoreSupplier

RETRY_AFTER = timedelta(minutes=5)

logger = logging.getLogger(__name__)


def decision_message(link: StoreSupplier, distributor: str):
    """(texto, botones) del aviso al tendero."""
    if link.status == SUPPLIER_APPROVED:
        route = parse_visit_weekdays(link.route_weekdays or "") or list(range(7))
        return texts.supplier_approved(distributor, route), keyboards.supplier_days_keyboard(link.id, route, [])
    return texts.supplier_rejected(distributor, link.rejection_reason or ""), None


async def notify_supplier_decision(bot: Bot, link_id: int) -> bool:
    async with SessionLocal() as session:
        link = await session.get(StoreSupplier, link_id)
        if link is None or link.status == SUPPLIER_PENDING or link.decision_notified_at is not None:
            return False
        store = await session.get(Store, link.store_id)
        if store is None or store.telegram_chat_id is None:
            return False  # no tiene el bot abierto: lo verá en /proveedores
        distributor = await session.scalar(select(Distributor.name).where(Distributor.id == link.distributor_id))
        text, markup = decision_message(link, distributor or "Tu proveedor")
        try:
            await bot.send_message(store.telegram_chat_id, text, reply_markup=markup)
        except Exception:
            # Solo ids: nunca el chat ni datos del tendero.
            logger.warning("No se pudo avisar la decisión del proveedor %s a la tienda %s", link.distributor_id, store.id)
            return False
        link.decision_notified_at = datetime.now(UTC)
        await session.commit()
        return True


async def send_supplier_followups(bot: Bot, now: datetime | None = None) -> int:
    """Tarea de fondo: reintenta los avisos de decisiones que no se pudieron mandar."""
    now = now or datetime.now(UTC)
    async with SessionLocal() as session:
        link_ids = (
            await session.scalars(
                select(StoreSupplier.id)
                .join(Store, Store.id == StoreSupplier.store_id)
                .where(
                    StoreSupplier.status != SUPPLIER_PENDING,
                    StoreSupplier.decision_notified_at.is_(None),
                    StoreSupplier.decided_at <= now - RETRY_AFTER,
                    Store.telegram_chat_id.is_not(None),
                )
            )
        ).all()
    sent = 0
    for link_id in link_ids:
        sent += await notify_supplier_decision(bot, link_id)
    return sent
