from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
    """SQLite ignora las foreign keys por defecto.

    Sin este PRAGMA, ON DELETE CASCADE y ON DELETE RESTRICT son decorativos:
    la base de datos acepta borrados que dejan filas huérfanas y nada falla.
    Es el fallo silencioso más caro de este motor.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")  # lecturas mientras se refrescan precios
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    future=True,
    connect_args={"check_same_thread": False} if settings.is_sqlite else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Dependencia de FastAPI. Una sesión por petición."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
