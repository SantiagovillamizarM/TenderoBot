"""Crea la cuenta de administrador de TenderoBot, le cambia la contraseña o la elimina.

Uso:
    python -m scripts.admin_user tu@correo.com              # crea la cuenta o le cambia la contraseña
    python -m scripts.admin_user tu@correo.com --eliminar   # quita esa cuenta de administrador

Cada correo tiene un solo papel: no se puede hacer administrador a un correo que ya es de un
proveedor o de un tendero. La contraseña se pide oculta: no se escribe en el comando ni queda en el
historial de la terminal. Desde la web no se pueden crear ni eliminar administradores.
"""

import argparse
import asyncio

import bcrypt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, engine
from app.models import AdminUser
from app.roles import email_role, other_role_error
from app.web.catalog import FormError
from app.web.store_forms import parse_email
from scripts.panel_user import ask_password


class AdminUserError(Exception):
    pass


async def find_admin(session: AsyncSession, email: str) -> AdminUser | None:
    return await session.scalar(select(AdminUser).where(func.lower(AdminUser.email) == email))


async def check_can_be_admin(session: AsyncSession, email: str) -> None:
    problem = other_role_error(await email_role(session, email), "admin")
    if problem:
        raise AdminUserError(problem)


async def save_admin(session: AsyncSession, email: str, password_hash: str) -> str:
    """Crea la cuenta o le cambia la contraseña. Devuelve "creada" o "actualizada"."""
    await check_can_be_admin(session, email)
    admin = await find_admin(session, email)
    if admin is None:
        session.add(AdminUser(email=email, password_hash=password_hash))
        return "creada"
    admin.password_hash = password_hash
    return "actualizada"


async def delete_admin(session: AsyncSession, email: str) -> None:
    admin = await find_admin(session, email)
    if admin is None:
        raise AdminUserError(f"{email} no es una cuenta de administrador.")
    remaining = await session.scalar(select(func.count(AdminUser.id)).where(AdminUser.id != admin.id))
    if not remaining:
        raise AdminUserError("Es la única cuenta de administrador: crea otra antes de eliminar esta.")
    await session.delete(admin)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Crea, actualiza o elimina una cuenta de administrador de TenderoBot.")
    parser.add_argument("email", help="correo del administrador")
    parser.add_argument("--eliminar", action="store_true", help="quita esta cuenta de administrador")
    args = parser.parse_args()
    try:
        email = parse_email(args.email)
    except FormError as exc:
        raise SystemExit(str(exc)) from exc

    try:
        async with SessionLocal() as session:
            if args.eliminar:
                if input(f"Para eliminar el administrador {email}, escribe el correo otra vez: ").strip().lower() != email:
                    raise SystemExit("No coincide. No se eliminó nada.")
                await delete_admin(session, email)
                await session.commit()
                print(f"Listo: {email} ya no es administrador.")
                return
            # Se revisa antes de pedir la contraseña, para no hacerla escribir en vano.
            if await find_admin(session, email) is None:
                await check_can_be_admin(session, email)
            password = ask_password()
            password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            action = await save_admin(session, email, password_hash)
            await session.commit()
        print(f"Listo: cuenta de administrador {email} {action}. Entra en /login.")
    except AdminUserError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
