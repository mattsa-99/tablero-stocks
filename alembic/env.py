from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from app.core.config import settings
from app.db import registry  # noqa: F401  -- importa TODOS los modelos
from app.db.alembic_support import COMPARE_OPTIONS
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """La URL de alembic.ini gana si está informada; si no, la de la app.

    Permite apuntar una migración puntual a otra base de datos
    (``-x`` o ``sqlalchemy.url``) sin tocar el entorno, y es lo que usan los
    tests de migraciones.
    """
    return config.get_main_option("sqlalchemy.url") or settings.database_url


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        # SQLite no soporta ALTER COLUMN ni DROP CONSTRAINT: el modo batch
        # recrea la tabla. Sin esto, casi cualquier migración que no sea un
        # CREATE TABLE falla. Es no-op en PostgreSQL.
        render_as_batch=_is_sqlite(url),
        **COMPARE_OPTIONS,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _database_url()
    connectable = create_engine(url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_is_sqlite(url),
            **COMPARE_OPTIONS,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
