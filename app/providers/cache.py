"""Compuerta de caché: TTL, backoff exponencial y single-flight.

Toda la metadata de frescura vive en `data_sync_state`, nunca en las tablas de
dominio. Vaciar esa tabla y las de mercado deja el sistema recuperable con un
refetch, sin perder ni un dato del usuario.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import random
import threading
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataSyncState

logger = logging.getLogger(__name__)

MAX_BACKOFF = dt.timedelta(hours=6)
BASE_BACKOFF_SECONDS = 60


class ResourceType:
    QUOTE = "quote"
    PRICE_HISTORY = "price_history"
    FUNDAMENTALS = "fundamentals"
    METADATA = "asset_metadata"
    FX = "fx"


# Single-flight EN PROCESO. Si dos peticiones concurrentes necesitan el mismo
# símbolo, solo una llama a Yahoo. No es distribuido: con varios workers cada
# uno tiene su propio lock, y el respaldo real frente a llamadas duplicadas
# sigue siendo el TTL persistido en la base de datos.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def flight_lock(resource_type: str, key: str) -> threading.Lock:
    name = f"{resource_type}:{key}"
    with _locks_guard:
        return _locks.setdefault(name, threading.Lock())


def payload_hash(payload: Any) -> str:
    """Hash estable del payload, para no reescribir datos idénticos."""
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class SyncGate:
    """Decide si toca ir a la red y registra el resultado."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def _state(self, resource_type: str, key: str) -> DataSyncState | None:
        return self.db.scalar(
            select(DataSyncState).where(
                DataSyncState.resource_type == resource_type,
                DataSyncState.resource_key == key,
            )
        )

    def _get_or_create(self, resource_type: str, key: str, ttl: int) -> DataSyncState:
        state = self._state(resource_type, key)
        if state is None:
            state = DataSyncState(
                resource_type=resource_type, resource_key=key, ttl_seconds=ttl
            )
            self.db.add(state)
            self.db.flush()
        return state

    def should_fetch(
        self, resource_type: str, key: str, ttl_seconds: int, *, force: bool = False
    ) -> bool:
        """True si el recurso está caducado y no está en periodo de backoff.

        `force` salta el TTL pero NO el backoff: forzar un refresco no debe
        poder convertirse en un martilleo contra un endpoint que ya está
        rechazando peticiones.
        """
        state = self._state(resource_type, key)
        if state is None:
            return True

        now = dt.datetime.now(dt.UTC)

        if state.next_eligible_at is not None and now < state.next_eligible_at:
            logger.debug(
                "%s:%s en backoff hasta %s", resource_type, key, state.next_eligible_at
            )
            return False

        if force or state.last_success_at is None:
            return True

        age = (now - state.last_success_at).total_seconds()
        return age >= ttl_seconds

    def mark_attempt(self, resource_type: str, key: str, ttl_seconds: int) -> None:
        state = self._get_or_create(resource_type, key, ttl_seconds)
        state.last_attempt_at = dt.datetime.now(dt.UTC)
        state.ttl_seconds = ttl_seconds

    def mark_success(
        self, resource_type: str, key: str, ttl_seconds: int, payload: Any = None
    ) -> bool:
        """Registra un fetch correcto. Devuelve True si el payload cambió."""
        state = self._get_or_create(resource_type, key, ttl_seconds)
        now = dt.datetime.now(dt.UTC)
        state.last_attempt_at = now
        state.last_success_at = now
        state.next_eligible_at = None
        state.consecutive_failures = 0
        state.last_error = None
        state.ttl_seconds = ttl_seconds

        if payload is None:
            return True
        new_hash = payload_hash(payload)
        changed = new_hash != state.payload_hash
        state.payload_hash = new_hash
        return changed

    def mark_failure(
        self, resource_type: str, key: str, ttl_seconds: int, error: Exception
    ) -> None:
        """Backoff exponencial con jitter.

        El jitter evita que todos los símbolos que fallaron a la vez vuelvan a
        intentarlo exactamente en el mismo instante y reproduzcan la ráfaga que
        provocó el rate limit.
        """
        state = self._get_or_create(resource_type, key, ttl_seconds)
        now = dt.datetime.now(dt.UTC)
        state.last_attempt_at = now
        state.consecutive_failures += 1
        state.last_error = str(error)[:500]

        delay = min(
            BASE_BACKOFF_SECONDS * (2 ** (state.consecutive_failures - 1)),
            MAX_BACKOFF.total_seconds(),
        )
        jitter = random.uniform(0, delay * 0.25)
        state.next_eligible_at = now + dt.timedelta(seconds=delay + jitter)

        logger.warning(
            "%s:%s falló (%d consecutivos), reintento tras %.0fs: %s",
            resource_type,
            key,
            state.consecutive_failures,
            delay + jitter,
            error,
        )
