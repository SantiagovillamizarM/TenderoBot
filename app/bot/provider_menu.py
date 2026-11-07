"""/proveedores: el tendero escoge con qué proveedores quiere trabajar.

1. Ve los proveedores activos que llegan a su ciudad, con lo que venden y cuántos productos tienen,
   y en qué va con cada uno (ya trabaja con él, en revisión, o lo rechazó antes).
2. Marca los que quiere con botones (puede varios) y toca "Enviar solicitudes".
3. A cada uno le llega una solicitud (StoreSupplier pendiente) a su panel. El proveedor acepta o
   rechaza (plan P4) y el bot le avisa al tendero.
4. "¿Quieres agregar otro?": puede volver a la lista cuando quiera con /proveedores.

Un proveedor llega a la tienda si su cobertura o su ciudad menciona la ciudad de la tienda. Si el
proveedor no registró zona, se muestra igual (marcado), para no esconder proveedores antiguos.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    SUPPLIER_APPROVED,
    SUPPLIER_PENDING,
    SUPPLIER_REJECTED,
    Distributor,
    Product,
    Store,
    StoreSupplier,
)
from app.providers import CATEGORIES

MAX_OPTIONS = 8  # botones por lista (Telegram limita el tamaño de los datos de cada botón)


@dataclass
class ProviderOption:
    id: int
    name: str
    categories: list[str]
    products: int
    status: str | None  # None (nunca le pidió), pending, approved o rejected
    zone_known: bool = True
    rejection_reason: str | None = None

    @property
    def selectable(self) -> bool:
        return self.status in (None, SUPPLIER_REJECTED)


def _fold(text: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", (text or "").casefold())
    return " ".join("".join(ch for ch in normalized if not unicodedata.combining(ch)).split())


_ZONE_SEPARATORS = re.compile(r"[,;/\n]|\s+y\s+")


def zone_places(distributor: Distributor) -> set[str]:
    """'Bucaramanga, Floridablanca y Girón' -> {'bucaramanga', 'floridablanca', 'giron'} (más su ciudad)."""
    raw = _ZONE_SEPARATORS.split(distributor.coverage or "") + [distributor.city or ""]
    return {place for place in (_fold(part).strip(" .") for part in raw) if place}


def covers(distributor: Distributor, city: str | None) -> bool:
    """¿El proveedor reparte en esa ciudad? Se compara la ciudad completa ("Gil" no es "San Gil").
    Sin ciudad de la tienda o sin zona registrada del proveedor: sí (no se puede saber)."""
    places = zone_places(distributor)
    wanted = _fold(city).strip(" .")
    if not places or not wanted:
        return True
    return wanted in places


def category_labels(raw: str | None) -> list[str]:
    return [CATEGORIES[key] for key in (raw or "").split(",") if key in CATEGORIES]


async def provider_options(session: AsyncSession, store: Store) -> list[ProviderOption]:
    """Proveedores activos que llegan a la tienda: primero con los que ya trabaja, luego los demás."""
    distributors = (
        await session.scalars(select(Distributor).where(Distributor.status == "active").order_by(Distributor.name))
    ).all()
    links = {
        link.distributor_id: link
        for link in (await session.scalars(select(StoreSupplier).where(StoreSupplier.store_id == store.id))).all()
    }
    counts = dict(
        (
            await session.execute(
                select(Product.distributor_id, func.count(Product.id))
                .where(Product.active.is_(True))
                .group_by(Product.distributor_id)
            )
        ).all()
    )
    options = []
    for distributor in distributors:
        link = links.get(distributor.id)
        # Con los que ya trabaja (o en revisión) se muestran aunque la zona no coincida.
        if link is None or link.status == SUPPLIER_REJECTED:
            if not covers(distributor, store.city):
                continue
        options.append(
            ProviderOption(
                id=distributor.id,
                name=distributor.name,
                categories=category_labels(distributor.categories),
                products=int(counts.get(distributor.id, 0)),
                status=link.status if link else None,
                zone_known=bool((distributor.coverage or "").strip() or (distributor.city or "").strip()),
                rejection_reason=link.rejection_reason if link else None,
            )
        )
    order = {SUPPLIER_APPROVED: 0, SUPPLIER_PENDING: 1, None: 2, SUPPLIER_REJECTED: 3}
    return sorted(options, key=lambda option: order[option.status])


async def request_providers(session: AsyncSession, store: Store, chosen: list[int]) -> list[str]:
    """Manda la solicitud a cada proveedor escogido. Devuelve los nombres de los que la recibieron."""
    options = {option.id: option for option in await provider_options(session, store)}
    now = datetime.now(UTC)
    sent = []
    for distributor_id in chosen:
        option = options.get(distributor_id)
        if option is None or not option.selectable:
            continue  # ya trabaja con él, ya está en revisión o no llega a su zona
        link = await session.scalar(
            select(StoreSupplier).where(StoreSupplier.store_id == store.id, StoreSupplier.distributor_id == distributor_id)
        )
        if link is None:
            session.add(StoreSupplier(store_id=store.id, distributor_id=distributor_id, status=SUPPLIER_PENDING,
                                      requested_at=now))
        else:
            # Lo había rechazado antes: la solicitud vuelve a revisión.
            link.status = SUPPLIER_PENDING
            link.requested_at = now
            link.decided_at = None
            link.rejection_reason = None
            link.decision_notified_at = None
        sent.append(option.name)
    return sent
