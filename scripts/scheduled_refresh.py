"""Refresco de datos de mercado SIN abrir la aplicación.

Pensado para lanzarse desde `launchd` cuatro veces por día hábil. Escribe en la
MISMA base local que usa la app (`tablero.db`), así que al abrir el tablero los
datos ya están frescos.

    python -m scripts.scheduled_refresh                # deduce la franja por la hora (ET)
    python -m scripts.scheduled_refresh --phase close  # fuerza una franja concreta
    python -m scripts.scheduled_refresh --force        # ejecuta aunque sea finde / fuera de hora

Franjas y qué hace cada una:

    pre-market    FX + cotizaciones de las posiciones abiertas
    apertura      cotizaciones + FX de todo el universo de ingesta
    media sesión  cotizaciones + FX de todo el universo de ingesta
    cierre        sincronización COMPLETA (barras diarias, fundamentales,
                  metadatos y FX)

Cualquier franja recupera además la sincronización completa si la última quedó
obsoleta: si el portátil estaba suspendido a la hora del cierre, launchd lanza
la tarea perdida al despertar y aquí se recoge.

No sale con código de error ante un fallo de Yahoo: se registra y se termina en
0 para que launchd no entre en un bucle de reintentos contra un endpoint que ya
rechaza peticiones. El backoff de `data_sync_state` es quien decide el reintento.

Festivos de la bolsa: NO se comprueban, igual que en el resto del sistema. Una
corrida en festivo gasta unas llamadas baratas y marca el TTL; el coste de
equivocarse es una llamada de más, nunca un dato incorrecto.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from zoneinfo import ZoneInfo

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.sync import SyncTrigger
from app.providers.yfinance_client import YFinanceClient
from app.services import ingestion

logger = logging.getLogger("scheduled_refresh")

# Todo el razonamiento de franjas va en hora del Este: es el mercado de
# referencia y el único que cambia de offset con el horario de verano. Los
# disparos de launchd se fijan en hora de Bogotá (sin DST) y caen en la franja
# correcta todo el año.
_ET = ZoneInfo("America/New_York")

_PHASES = ("premarket", "open", "midday", "close")


def _phase_for(now_et: dt.datetime) -> str | None:
    """Franja de mercado según la hora del Este, o None fuera de todas."""
    minutes = now_et.hour * 60 + now_et.minute
    if 4 * 60 <= minutes < 9 * 60 + 15:
        return "premarket"
    if 9 * 60 + 15 <= minutes < 11 * 60 + 30:
        return "open"
    if 11 * 60 + 30 <= minutes < 15 * 60 + 30:
        return "midday"
    if 15 * 60 + 30 <= minutes < 20 * 60:
        return "close"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=[*_PHASES, "auto"], default="auto")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ejecuta aunque sea fin de semana o fuera del horario bursátil",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
    )

    now_et = dt.datetime.now(_ET)

    if now_et.weekday() >= 5 and not args.force:
        logger.info("Fin de semana en Nueva York (%s): nada que refrescar", now_et.date())
        return 0

    phase = args.phase if args.phase != "auto" else _phase_for(now_et)
    if phase is None:
        # launchd disparó una tarea perdida a deshora (equipo recién despierto).
        # Solo tiene sentido comprobar si la sincronización completa quedó atrás.
        phase = "catchup"

    logger.info("Refresco de mercado: franja=%s hora_ET=%s", phase, now_et.strftime("%H:%M"))

    db = SessionLocal()
    try:
        provider = YFinanceClient(settings.provider_timeout_seconds)

        if phase == "close":
            ingestion.run_sync(db, provider, trigger=SyncTrigger.SCHEDULED)
            return 0

        if phase == "premarket":
            ingestion.run_intraday_refresh(db, provider, scope="positions")
        elif phase in ("open", "midday"):
            ingestion.run_intraday_refresh(db, provider, scope="universe")

        if ingestion.is_sync_due(db):
            logger.info("La sincronización completa quedó pendiente: se recupera ahora")
            ingestion.run_sync(db, provider, trigger=SyncTrigger.SCHEDULED)
    except Exception:
        logger.exception("El refresco de mercado falló")
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
