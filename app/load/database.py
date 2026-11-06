"""Base de datos separada para la semana de carga (S4).

Las miles de ventas de la carga no pueden caer en la base real (Neon), donde están las tiendas de
verdad. Por eso todo lo de la carga corre contra LOAD_DATABASE_URL.

`use_load_database()` debe llamarse ANTES de importar app.db (o cualquier módulo que lo importe,
como app.bot.handlers): pone DATABASE_URL apuntando a la base de carga, y como una variable de
entorno tiene prioridad sobre el .env, toda la app (y Alembic) usa esa base.

Candado: se niega si la base de carga es la misma que la del .env.
"""

import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ENV_FILE = Path(".env")
# PostgreSQL local de docker-compose.carga.yml.
DEFAULT_LOAD_URL = "postgresql+asyncpg://carga:carga@localhost:55432/tenderobot_carga"


class LoadDatabaseError(Exception):
    pass


def _env_file_value(key: str) -> str:
    if not ENV_FILE.is_file():
        return ""
    return str(dotenv_values(ENV_FILE).get(key) or "").strip()


def _identity(url: str) -> tuple[str, int | None, str]:
    parsed = make_url(url)
    return ((parsed.host or "").lower(), parsed.port, (parsed.database or "").lower())


def load_database_url() -> str:
    return (os.environ.get("LOAD_DATABASE_URL") or _env_file_value("LOAD_DATABASE_URL") or DEFAULT_LOAD_URL).strip()


def check_separate(load_url: str, main_url: str) -> None:
    """Error si la base de carga es la misma que la principal (mismo servidor, puerto y base)."""
    if not main_url:
        return
    if _identity(load_url) == _identity(main_url):
        raise LoadDatabaseError(
            "LOAD_DATABASE_URL apunta a la misma base que DATABASE_URL. La carga necesita una base aparte "
            "para no mezclar sus ventas con las tiendas reales."
        )


def use_load_database() -> str:
    """Hace que la app use la base de carga. Devuelve su URL (sin contraseña) para mostrarla."""
    load_url = load_database_url()
    # Se compara con la base del .env y con la de la variable de entorno, si alguien la exportó.
    for main_url in {_env_file_value("DATABASE_URL"), os.environ.get("DATABASE_URL", "")}:
        check_separate(load_url, main_url)
    if "app.db" in sys.modules:
        raise LoadDatabaseError("use_load_database() debe llamarse antes de importar app.db")
    os.environ["DATABASE_URL"] = load_url
    from app.config import get_settings

    get_settings.cache_clear()
    return make_url(load_url).render_as_string(hide_password=True)
