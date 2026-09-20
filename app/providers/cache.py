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
    # El HISTÓRICO de un par, distinto del tipo del día: su TTL se mide en
    # días, no en minutos, porque son cierres ya cerrados.
    FX_HISTORY = "fx_history"
    # El proveedor COMO UN TODO, no un símbolo. Un 429 no dice nada sobre el
    # activo que se pidió: dice que hay que parar de pedir.
    PROVIDER = "provider"


PROVIDER_KEY = "yahoo"

# Enfriamiento tras un 429. Arranca más alto que `BASE_BACKOFF_SECONDS` porque
# un rate limit no es un fallo puntual que convenga reintentar en un minuto:
# es el proveedor diciendo que el ritmo es insostenible.
RATE_LIMIT_COOLDOWN_SECONDS = 900

# Enfriamiento cuando no hay conexión. MUCHO más corto y con techo propio, y
# las dos cosas por la misma razón: aquí el proveedor no ha pedido nada.
#
# Un 429 es Yahoo diciendo "para"; obedecer de más solo cuesta frescura. Una
# caída de red es un problema NUESTRO -el portátil se suspendió, el wifi se
# fue- y puede resolverse en cualquier segundo, así que castigarse seis horas
# por una desconexión de treinta segundos desperdiciaría las corridas
# siguientes del agente.
#
# El techo bajo además evita un bloqueo: mientras el enfriamiento está activo
# no se llama a nadie, y si no se llama a nadie no hay respuesta correcta que
# pueda cerrarlo. Con 15 minutos como máximo, una máquina que despierta vuelve
# a intentarlo dentro de la misma franja de mercado.
UNREACHABLE_COOLDOWN_SECONDS = 120
UNREACHABLE_COOLDOWN_MAX = 900


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

        # Cualquier respuesta buena demuestra que el proveedor volvió: se
        # cierra el enfriamiento aquí y no solo en cotizaciones, porque el
        # 429 lo puede haber disparado cualquiera de los cinco recursos.
        if resource_type != ResourceType.PROVIDER:
            self.clear_rate_limit()

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

    # ------------------------------------------------------------------
    # Enfriamiento global del proveedor
    # ------------------------------------------------------------------

    def _cool_down(
        self, error: Exception, base_seconds: int, max_seconds: float, label: str
    ) -> dt.datetime:
        """Enfría el proveedor entero durante un rato que crece con la racha."""
        state = self._get_or_create(ResourceType.PROVIDER, PROVIDER_KEY, base_seconds)
        now = dt.datetime.now(dt.UTC)
        state.last_attempt_at = now
        state.consecutive_failures += 1
        state.last_error = str(error)[:500]

        delay = min(base_seconds * (2 ** (state.consecutive_failures - 1)), max_seconds)
        jitter = random.uniform(0, delay * 0.25)
        state.next_eligible_at = now + dt.timedelta(seconds=delay + jitter)
        logger.warning(
            "%s (%d consecutivos): no se pide nada hasta %s",
            label, state.consecutive_failures, state.next_eligible_at,
        )
        return state.next_eligible_at

    def mark_unreachable(self, error: Exception) -> dt.datetime:
        """Apunta la caída de RED contra el proveedor, no contra los símbolos.

        Misma lógica que `mark_rate_limited` y, por volumen, el caso que de
        verdad ocurre: en el log del agente hay 1.282 fallos de DNS frente a
        23 de rate limit. Un `Could not resolve host` no dice absolutamente
        nada sobre el ticker que tocaba pedir, y cargárselo lo dejaba en
        backoff exponencial como si estuviera roto -25 símbolos idénticos por
        lote, 494 en una sincronización entera-.

        Además corta en seco la sincronización que ya no puede funcionar: la
        del 18 de septiembre estuvo 67 minutos fallando contra una red que no
        estaba, y terminó con 0 cotizaciones, 0 barras y 0 fundamentales.
        """
        return self._cool_down(
            error,
            UNREACHABLE_COOLDOWN_SECONDS,
            UNREACHABLE_COOLDOWN_MAX,
            "Sin conexión con el proveedor",
        )

    def mark_rate_limited(self, error: Exception) -> dt.datetime:
        """Apunta el 429 CONTRA EL PROVEEDOR, no contra los símbolos pedidos.

        Es la diferencia entre degradar y averiarse. Cuando Yahoo corta a
        mitad de un lote, cargarle el fallo a cada símbolo les sube el
        contador de fallos consecutivos y los mete en un backoff exponencial
        propio, como si el ticker estuviera roto. Medido en este sistema: las
        sincronizaciones del 16, 17 y 18 de septiembre marcaron 469, 420 y 494
        símbolos como fallidos, y a partir de ahí el universo entero quedaba
        en penitencia por un límite que no tenía nada que ver con él.

        Aquí el castigo va a una sola fila. Cuando expira, los 494 símbolos
        vuelven a estar disponibles a la vez porque nunca dejaron de estarlo.
        """
        return self._cool_down(
            error,
            RATE_LIMIT_COOLDOWN_SECONDS,
            MAX_BACKOFF.total_seconds(),
            "Rate limit del proveedor",
        )

    def provider_cooldown_until(self) -> dt.datetime | None:
        """Instante hasta el que NO hay que llamar al proveedor, o None."""
        state = self._state(ResourceType.PROVIDER, PROVIDER_KEY)
        if state is None or state.next_eligible_at is None:
            return None
        if dt.datetime.now(dt.UTC) >= state.next_eligible_at:
            return None
        return state.next_eligible_at

    def clear_rate_limit(self) -> None:
        """Una llamada correcta cierra el enfriamiento.

        Sin esto el contador de 429 solo sube y el enfriamiento acabaría en el
        techo de 6 horas aunque el proveedor lleve días respondiendo bien.
        """
        state = self._state(ResourceType.PROVIDER, PROVIDER_KEY)
        if state is None or state.consecutive_failures == 0:
            return
        state.consecutive_failures = 0
        state.next_eligible_at = None
        state.last_success_at = dt.datetime.now(dt.UTC)
