"""Proveedores de una tienda (StoreSupplier): una tienda puede trabajar con varios.

Mientras el resto del código pasa a leer de aquí (planes P3 a P6), los días de ruta, de entrega y el
recordatorio del proveedor principal siguen también en Store. `mirror_store_link` copia esos datos a
StoreSupplier cada vez que cambian, para que las dos fuentes nunca se desencuentren.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SUPPLIER_APPROVED, Store, StoreSupplier

MIRRORED_FIELDS = ("route_weekdays", "visit_weekdays", "lead_time_days", "delivery_days_set_at", "last_reminder_for")


async def mirror_store_link(session: AsyncSession, store: Store) -> StoreSupplier | None:
    """Deja el proveedor principal de la tienda como proveedor aceptado, con los días de la tienda."""
    if store.distributor_id is None:
        return None
    if store.id is None:
        await session.flush()
    link = await session.scalar(
        select(StoreSupplier).where(
            StoreSupplier.store_id == store.id, StoreSupplier.distributor_id == store.distributor_id
        )
    )
    if link is None:
        now = datetime.now(UTC)
        link = StoreSupplier(
            store_id=store.id,
            distributor_id=store.distributor_id,
            status=SUPPLIER_APPROVED,
            decided_at=now,
            # Ya está trabajando con este proveedor: no hay decisión que avisarle.
            decision_notified_at=now,
        )
        session.add(link)
    elif link.status != SUPPLIER_APPROVED:
        link.status = SUPPLIER_APPROVED
        link.decided_at = datetime.now(UTC)
        link.rejection_reason = None
    for field in MIRRORED_FIELDS:
        setattr(link, field, getattr(store, field))
    return link


async def approved_suppliers(session: AsyncSession, store_id: int) -> list[StoreSupplier]:
    """Proveedores aceptados de la tienda, del más antiguo al más nuevo."""
    return list(
        (
            await session.scalars(
                select(StoreSupplier)
                .where(StoreSupplier.store_id == store_id, StoreSupplier.status == SUPPLIER_APPROVED)
                .order_by(StoreSupplier.decided_at, StoreSupplier.id)
            )
        ).all()
    )
