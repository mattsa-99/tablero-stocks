"""Refrescos que corren FUERA del ciclo de la petición.

Por qué existe este módulo. La vista de oportunidades llamaba a
`full_refresh` -el job cuyo propio docstring dice "cadencia objetivo: dos
veces por semana"- dentro de la petición HTTP y sobre los 494 símbolos del
universo. Medido el 19-09-2026 sobre la cartera real: **269 s** con la página
esperando, frente a **0,23 s** de puntuar lo que ya está en la base. Mil
veces más caro para, casi siempre, llegar a los mismos datos.

La corrección no es "refrescar menos", es refrescar en otro sitio:

1. La petición responde con lo que hay guardado y dice si se está
   refrescando. Nunca espera a la red.
2. El refresco corre después de enviar la respuesta, con su propia sesión.
3. Un candado no bloqueante garantiza uno a la vez: abrir la vista cinco
   veces seguidas no lanza cinco sincronizaciones.

**Solo cotizaciones y tipos de cambio**, no el job completo. De todo lo que
toca `full_refresh`, lo único que cambia intradía y le importa al ranking es
el PRECIO -de ahí sale `fresh_trailing_pe`-. Las barras diarias son cierres
inmutables, los fundamentales son trimestrales y los metadatos tienen un TTL
de 30 días: pedirlos al abrir una página no mejora ningún número y es lo que
agotaba el presupuesto de peticiones. El job completo sigue siendo del
planificador.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import sessionmaker

from app.providers.base import MarketProvider
from app.providers.cache import flight_lock
from app.repositories import market as market_repo
from app.services.market_data import MarketDataService

logger = logging.getLogger(__name__)

_LOCK_RESOURCE = "background_refresh"


def refresh_running(scope: str) -> bool:
    """True si ya hay un refresco de fondo en curso para ese ámbito.

    Se consulta el candado en vez de llevar una bandera aparte: una bandera y
    un candado se desincronizan en cuanto una excepción se salta el `finally`,
    y entonces la interfaz se queda esperando para siempre a un refresco que
    ya terminó.
    """
    return flight_lock(_LOCK_RESOURCE, scope).locked()


def refresh_quotes_for(
    session_factory: sessionmaker,
    provider: MarketProvider,
    symbols: list[str],
    base_currency: str,
    *,
    scope: str,
) -> None:
    """Refresca cotizaciones y FX de esos símbolos. Pensado para ir de fondo.

    Recibe SÍMBOLOS y no activos: los objetos ORM de la petición pertenecen a
    una sesión que ya está cerrada cuando esto arranca, y usarlos aquí lanza
    `DetachedInstanceError`. Se vuelven a buscar en la sesión propia.

    No propaga nunca: esto corre después de haber respondido, así que no hay
    a quién devolverle el error. Lo que falle se registra y el siguiente
    intento lo reintentará con su TTL y su backoff.
    """
    if not symbols:
        return

    lock = flight_lock(_LOCK_RESOURCE, scope)
    if not lock.acquire(blocking=False):
        logger.info("Refresco de fondo ya en curso para %s: no se duplica", scope)
        return

    session = session_factory()
    try:
        assets = list(market_repo.get_assets_by_symbols(session, symbols).values())
        if not assets:
            return
        report = MarketDataService(session, provider).refresh_quotes_and_fx(
            assets, [base_currency]
        )
        logger.info(
            "Refresco de fondo (%s): %d cotizaciones, %d tipos de cambio, %d avisos",
            scope, report.quotes_updated, report.fx_updated, len(report.warnings),
        )
    except Exception:
        # Un fallo aquí no puede tumbar el worker: ya se respondió al usuario.
        logger.exception("Falló el refresco de fondo de %s", scope)
    finally:
        session.close()
        lock.release()
