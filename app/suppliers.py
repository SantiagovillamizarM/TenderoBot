"""Proveedores de una tienda (StoreSupplier): una tienda puede trabajar con varios.

Mientras el resto del código pasa a leer de aquí (planes P3 a P6), los días de ruta, de entrega y el
recordatorio del proveedor principal siguen también en Store. `mirror_store_link` copia esos datos a
StoreSupplier cada vez que cambian, para que las dos fuentes nunca se desencuentren.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SUPPLIER_APPROVED, SUPPLIER_REJECTED, Store, StoreSupplier

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


def approve_supplier(link: StoreSupplier, store: Store, route_days: list[int], lead_time_days: int) -> None:
    """El proveedor acepta trabajar con la tienda, con sus días de ruta y su anticipación.

    Los días de entrega los escoge el tendero en el bot (hasta entonces quedan todos los de la ruta).
    El primer proveedor que acepta queda como principal de la tienda: así ventas, /pedido y demás
    funcionan ya con el código de hoy (el pedido por proveedor llega en el plan P6).
    """
    days = ",".join(str(day) for day in sorted(set(route_days)))
    now = datetime.now(UTC)
    link.status = SUPPLIER_APPROVED
    link.decided_at = now
    link.rejection_reason = None
    link.decision_notified_at = None
    link.route_weekdays = days
    link.visit_weekdays = days
    link.lead_time_days = lead_time_days
    link.delivery_days_set_at = None
    if store.distributor_id is None:
        store.distributor_id = link.distributor_id
        store.route_weekdays = days
        store.visit_weekdays = days
        store.lead_time_days = lead_time_days
        store.delivery_days_set_at = None


def reject_supplier(link: StoreSupplier, reason: str) -> None:
    link.status = SUPPLIER_REJECTED
    link.decided_at = datetime.now(UTC)
    link.rejection_reason = reason
    link.decision_notified_at = None


def set_supplier_days(link: StoreSupplier, store: Store, days: list[int]) -> None:
    """Días de entrega que escoge el tendero con este proveedor (dentro de su ruta)."""
    link.visit_weekdays = ",".join(str(day) for day in sorted(set(days)))
    link.delivery_days_set_at = datetime.now(UTC)
    if store.distributor_id == link.distributor_id:
        # Es el principal: la tienda guarda la misma copia (hasta el plan P6).
        store.visit_weekdays = link.visit_weekdays
        store.delivery_days_set_at = link.delivery_days_set_at


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
