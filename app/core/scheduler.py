"""Planificador del pipeline de ingesta.

Un cron diario tras el cierre de NYSE, con APScheduler. Se prefiere a un bucle
`asyncio.sleep` hecho a mano por dos razones concretas, no por gusto:

1. **Horario de verano.** "18:00 en America/New_York" cambia de offset UTC dos
   veces al año. Un bucle que duerme 24 h se desalinea una hora en marzo y otra
   en noviembre; `CronTrigger` con timezone lo resuelve.
2. **Misfires.** `coalesce=True` + `misfire_grace_time` evitan que un portátil
   que despierta tras dos días dispare dos ejecuciones seguidas.

Lo que APScheduler NO resuelve, y sí importa en una app auto-alojada: si la
máquina está apagada a las 18:00 ET, ese disparo simplemente no existe. Por eso
hay además una comprobación de recuperación al arrancar, basada en
`sync_runs` -que es persistente- y no en un contador en memoria.
"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.sync import SyncTrigger
from app.providers.yfinance_client import YFinanceClient
from app.services import catalog, ingestion, universe

logger = logging.getLogger(__name__)

SYNC_JOB_ID = "daily_market_sync"

_scheduler: AsyncIOScheduler | None = None


def _sync(trigger: SyncTrigger) -> None:
    """Ejecuta una sincronización con su propia sesión.

    Sesión propia y no la de una petición: este código corre fuera del ciclo
    request/response y no puede compartir una sesión con nadie.
    """
    db = SessionLocal()
    try:
        provider = YFinanceClient(settings.provider_timeout_seconds)
        ingestion.run_sync(db, provider, trigger=trigger)
    finally:
        db.close()


async def _scheduled_sync() -> None:
    """Disparo del cron. El trabajo real va a un hilo aparte.

    La ingesta es I/O bloqueante (requests + SQLite). Ejecutarla en el event
    loop congelaría toda la API durante los minutos que dura.
    """
    await asyncio.to_thread(_sync, SyncTrigger.SCHEDULED)


async def _startup_catchup() -> None:
    """Recupera la sincronización si la última es demasiado antigua."""
    db = SessionLocal()
    try:
        if not ingestion.is_sync_due(db):
            logger.info("Sincronización al día: no se recupera nada al arrancar")
            return
        logger.info("Última sincronización antigua o inexistente: recuperando")
    finally:
        db.close()

    await asyncio.to_thread(_sync, SyncTrigger.STARTUP_CATCHUP)


def _seed() -> None:
    """Siembra catálogo y universo. Son dos conjuntos distintos.

    El CATÁLOGO entra completo pero como buscable, no ingestable: promoverlo
    convertiría la sincronización diaria en cientos de llamadas. El UNIVERSO
    -lo que sí se refresca a diario- sigue siendo la lista corta.
    """
    db = SessionLocal()
    try:
        if settings.seed_catalog_on_startup:
            catalog.seed_catalog(db, promote_to_universe=False)
        universe.seed_universe(db, universe.default_symbols())
    finally:
        db.close()


def start_scheduler() -> AsyncIOScheduler | None:
    """Arranca el planificador. Devuelve None si está desactivado."""
    global _scheduler

    if not settings.enable_background_refresh:
        logger.info("Refresco en segundo plano desactivado por configuración")
        return None

    if settings.seed_universe_on_startup:
        try:
            _seed()
        except Exception:
            # Sembrar el universo NO puede impedir que la API arranque: falla
            # si la base aún no está migrada, y en ese caso el usuario necesita
            # que el servidor levante para poder diagnosticarlo.
            logger.exception("No se pudo sembrar el universo; se continúa sin él")

    scheduler = AsyncIOScheduler(timezone=settings.sync_timezone)
    scheduler.add_job(
        _scheduled_sync,
        trigger=CronTrigger(
            day_of_week=settings.sync_days,
            hour=settings.sync_hour,
            minute=settings.sync_minute,
            timezone=settings.sync_timezone,
        ),
        id=SYNC_JOB_ID,
        name="Sincronización diaria de datos de mercado",
        # Si el disparo se pierde por menos de una hora (proceso ocupado,
        # máquina lenta), se ejecuta igual; más allá se descarta y lo recoge la
        # recuperación de arranque.
        misfire_grace_time=3600,
        # Varios disparos perdidos se colapsan en UNO. Sin esto, un portátil
        # que despierta tras una semana lanzaría cinco sincronizaciones
        # simultáneas contra un proveedor con rate limit.
        coalesce=True,
        max_instances=1,
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler

    job = scheduler.get_job(SYNC_JOB_ID)
    logger.info(
        "Planificador activo: %s %02d:%02d %s. Próxima ejecución: %s",
        settings.sync_days,
        settings.sync_hour,
        settings.sync_minute,
        settings.sync_timezone,
        job.next_run_time if job else "desconocida",
    )
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def next_run_time() -> str | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job(SYNC_JOB_ID)
    return job.next_run_time.isoformat() if job and job.next_run_time else None


async def run_catchup_in_background() -> None:
    """Lanza la recuperación de arranque sin bloquear el arranque de la app."""
    try:
        await _startup_catchup()
    except Exception:
        # Un fallo aquí no puede impedir que la API arranque.
        logger.exception("Fallo en la recuperación de arranque")
