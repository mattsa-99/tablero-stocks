"""Sesiones de base de datos para las herramientas.

SOLO LECTURA POR DEFECTO, y no por convención sino por construcción: la sesión
se cierra con `rollback()` pase lo que pase. Una herramienta de lectura que
accidentalmente escriba no deja rastro.

La única que escribe (`journal_write`) pide explícitamente la sesión que
confirma, y eso la hace visible al leer el código.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.db.session import SessionLocal


@contextmanager
def read_only() -> Iterator[Session]:
    """Sesión que NUNCA confirma. Todo lo que escriba se descarta al salir."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        db.close()


@contextmanager
def writable() -> Iterator[Session]:
    """Sesión que confirma si no hubo excepción. Solo para el diario."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
