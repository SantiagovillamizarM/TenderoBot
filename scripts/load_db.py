"""Prepara y revisa la base separada de la semana de carga (S4).

    docker compose -f docker-compose.carga.yml up -d   # PostgreSQL local (una sola vez)
    python -m scripts.load_db setup                     # tablas + catálogo + 3 tiendas con 60 días de historia
                                                        # + tablas de la carga (corridas, unidades, señales de vida)
    python -m scripts.load_db estado                    # qué hay en la base de carga

Usa LOAD_DATABASE_URL (del entorno o del .env; por defecto el PostgreSQL local de
docker-compose.carga.yml) y se niega a correr si es la misma base que DATABASE_URL.
`setup` borra y vuelve a crear los datos del distribuidor de prueba en la base de carga (las corridas
anteriores se conservan).
"""

import argparse
import asyncio
import os
import secrets
import subprocess
import sys

from app.load.database import LoadDatabaseError, use_load_database


def prepare_environment() -> str:
    # La cuenta del panel que exige el seed: en la base de carga no se usa, pero debe ser fuerte.
    os.environ.setdefault("PANEL_EMAIL", "carga@tenderobot.local")
    os.environ.setdefault("PANEL_PASSWORD", secrets.token_urlsafe(24))
    try:
        return use_load_database()
    except LoadDatabaseError as exc:
        raise SystemExit(str(exc)) from exc


async def store_summary() -> list[tuple]:
    from sqlalchemy import func, select

    from app.db import SessionLocal, engine
    from app.models import Distributor, LlmUsage, Product, Sale, Store
    from scripts.seed import DISTRIBUTOR_NAME

    async with SessionLocal() as session:
        distributor = await session.scalar(select(Distributor).where(Distributor.name == DISTRIBUTOR_NAME))
        if distributor is None:
            await engine.dispose()
            return []
        stores = (
            await session.scalars(select(Store).where(Store.distributor_id == distributor.id).order_by(Store.id))
        ).all()
        rows = []
        for index, store in enumerate(stores):
            sales = await session.scalar(select(func.count(Sale.id)).where(Sale.store_id == store.id))
            rows.append((index, store.id, store.name, sales))
        products = await session.scalar(select(func.count(Product.id)).where(Product.distributor_id == distributor.id))
        usage = await session.scalar(select(func.count(LlmUsage.id)))
    await engine.dispose()
    return [("productos", products), ("llamadas_en_bitacora", usage)] + rows


def setup() -> None:
    url = prepare_environment()
    print(f"Base de carga: {url}")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=os.environ.copy())
    subprocess.run([sys.executable, "-m", "scripts.seed", "--demo"], check=True, env=os.environ.copy())
    asyncio.run(create_load_tables())
    print_summary()


async def create_load_tables() -> None:
    """Tablas de la carga (corridas, unidades, señales de vida): solo en esta base, no en las migraciones."""
    from app.db import engine
    from app.load.models import LoadBase

    async with engine.begin() as connection:
        await connection.run_sync(LoadBase.metadata.create_all)
    await engine.dispose()


def print_summary() -> None:
    rows = asyncio.run(store_summary())
    if not rows:
        print("La base de carga no tiene el distribuidor de prueba: corre `python -m scripts.load_db setup`.")
        return
    (_, products), (_, usage), *stores = rows
    print(f"{products} productos · {usage} llamadas en la bitácora")
    for index, store_id, name, sales in stores:
        print(f"  tienda {index} → id {store_id}: {name} ({sales} ventas)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("accion", choices=["setup", "estado"])
    args = parser.parse_args()
    if args.accion == "setup":
        setup()
    else:
        print(f"Base de carga: {prepare_environment()}")
        print_summary()


if __name__ == "__main__":
    main()
