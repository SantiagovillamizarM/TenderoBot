"""Un solo papel por correo: administrador, proveedor o tendero.

Lo revisan todos los lugares donde se crea una cuenta o una solicitud: la página pública, el panel
de administración (cuentas de proveedor) y los scripts (admin_user, panel_user, seed).
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminUser, DistributorRequest, DistributorUser, Store, StoreRequest

ROLE_ERRORS = {
    "admin": "Ese correo es de una cuenta de administrador de TenderoBot. Usa otro correo.",
    "provider": "Ese correo es de un proveedor. Para pedir acceso como tendero, usa otro correo.",
    "tendero": "Ese correo es de un tendero. Para registrar una empresa proveedora, usa otro correo.",
}
ROLE_NAMES = {"admin": "administrador", "provider": "proveedor", "tendero": "tendero"}


async def email_role(session: AsyncSession, email: str) -> str | None:
    """Qué es este correo en TenderoBot. Cada correo tiene un solo papel: administrador, proveedor o tendero.

    Una solicitud rechazada no cuenta: puede volver a intentarlo.
    """
    email = email.strip().lower()
    if await session.scalar(select(AdminUser.id).where(func.lower(AdminUser.email) == email)):
        return "admin"
    if await session.scalar(select(DistributorUser.id).where(func.lower(DistributorUser.email) == email)) or (
        await session.scalar(
            select(DistributorRequest.id).where(
                func.lower(DistributorRequest.contact_email) == email,
                DistributorRequest.status.in_(("pending", "approved")),
            )
        )
    ):
        return "provider"
    if await session.scalar(select(Store.id).where(func.lower(Store.email) == email)) or (
        await session.scalar(
            select(StoreRequest.id).where(
                func.lower(StoreRequest.email) == email, StoreRequest.status.in_(("pending", "approved"))
            )
        )
    ):
        return "tendero"
    return None


def other_role_error(role: str | None, wanted: str) -> str | None:
    """Mensaje si el correo ya tiene un papel distinto del que se quiere darle (None si se puede)."""
    if role is None or role == wanted:
        return None
    return f"Ese correo ya es de un {ROLE_NAMES[role]} en TenderoBot: cada correo tiene un solo papel. Usa otro correo."
