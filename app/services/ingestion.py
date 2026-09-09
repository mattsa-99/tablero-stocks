"""Pipeline de ingesta: orquesta una sincronización completa y la registra.

Envuelve a `MarketDataService` añadiendo lo que un pipeline necesita y un
refresco suelto no tiene: un registro persistente de cada ejecución, con qué
escribió y qué falló. Ese registro es lo que alimenta la UI ("última
sincronización") y lo que permite al planificador saber si debe recuperar una
ejecución perdida.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Iterator
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Asset, Portfolio, PriceHistory
from app.models.sync import SyncRun, SyncStatus, SyncTrigger
from app.providers.base import MarketProvider
from app.services import universe as universe_service
from app.services.market_data import MarketDataService, RefreshReport

logger = logging.getLogger(__name__)

MAX_WARNINGS_STORED = 40


def _batches(items: list, size: int) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def last_successful_run(db: Session) -> SyncRun | None:
    return db.scalar(
        select(SyncRun)
        .where(SyncRun.status.in_([SyncStatus.SUCCESS, SyncStatus.PARTIAL]))
        .order_by(SyncRun.started_at.desc())
        .limit(1)
    )


def last_run(db: Session) -> SyncRun | None:
    return db.scalar(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))


def is_sync_due(db: Session) -> bool:
    """True si nunca se sincronizó o la última fue hace demasiado."""
    latest = last_successful_run(db)
    if latest is None or latest.finished_at is None:
        return True
    age = dt.datetime.now(dt.UTC) - latest.finished_at
    return age > dt.timedelta(hours=settings.sync_catchup_after_hours)


def _base_currencies(db: Session) -> list[str]:
    currencies = sorted(
        {
            row
            for row in db.scalars(select(Portfolio.base_currency).distinct()).all()
            if row
        }
    )
    return currencies or [settings.default_base_currency]


def run_sync(
    db: Session,
    provider: MarketProvider,
    *,
    trigger: SyncTrigger = SyncTrigger.MANUAL,
    force: bool = False,
    symbols: list[str] | None = None,
) -> SyncRun:
    """Ejecuta el pipeline y devuelve el registro de la ejecución.

    El `SyncRun` se persiste con estado RUNNING ANTES de empezar, no al final:
    si el proceso muere a mitad, queda constancia de que hubo un intento en
    lugar de un silencio que parece "nunca se sincronizó".
    """
    run = SyncRun(
        trigger=trigger,
        status=SyncStatus.RUNNING,
        started_at=dt.datetime.now(dt.UTC),
    )
    db.add(run)
    db.commit()

    try:
        if symbols:
            assets = _assets_for_symbols(db, symbols)
        else:
            assets = universe_service.get_ingestion_universe(db)

        run.symbols_requested = len(assets)
        db.commit()

        if not assets:
            run.status = SyncStatus.SUCCESS
            run.finished_at = dt.datetime.now(dt.UTC)
            run.warnings = "El universo está vacío: nada que sincronizar"
            db.commit()
            return run

        service = MarketDataService(db, provider)
        currencies = _base_currencies(db)

        # LOTES CON PAUSA. Con un universo de decenas cabría hacerlo de una vez,
        # pero el sistema está pensado para crecer y yfinance no publica sus
        # límites: al superarlos empieza a devolver 429 y el backoff deja el
        # símbolo bloqueado horas. Trocear cuesta unos segundos de reloj y
        # evita perder una sincronización entera.
        #
        # La pausa va ENTRE lotes, no después del último: dormir al final solo
        # alarga el trabajo sin proteger nada.
        report = RefreshReport()
        batch_size = max(1, settings.ingestion_batch_size)
        delay = max(0.0, settings.ingestion_batch_delay_seconds)
        chunks = list(_batches(assets, batch_size))

        for index, chunk in enumerate(chunks):
            logger.info(
                "Sincronizando lote %d/%d (%d símbolos)",
                index + 1, len(chunks), len(chunk),
            )
            report.merge(service.full_refresh(chunk, currencies, force=force))
            if delay and index < len(chunks) - 1:
                time.sleep(delay)

        run.quotes_updated = report.quotes_updated
        run.bars_written = report.bars_written
        run.fundamentals_updated = report.fundamentals_updated
        run.fx_updated = report.fx_updated
        run.metadata_updated = report.metadata_updated
        run.symbols_failed = len(set(report.failed_symbols))
        run.warnings = (
            "\n".join(report.warnings[:MAX_WARNINGS_STORED]) if report.warnings else None
        )

        # PARTIAL y no FAILED cuando fallan algunos símbolos: el resto del
        # universo SÍ se actualizó, y presentarlo como fallo total llevaría a
        # descartar datos buenos.
        if run.symbols_failed == 0:
            run.status = SyncStatus.SUCCESS
        elif run.symbols_failed >= len(assets):
            run.status = SyncStatus.FAILED
        else:
            run.status = SyncStatus.PARTIAL

    except Exception as exc:
        logger.exception("La sincronización falló")
        run.status = SyncStatus.FAILED
        run.warnings = str(exc)[:2000]
    finally:
        run.finished_at = dt.datetime.now(dt.UTC)
        db.commit()
        db.refresh(run)

    logger.info(
        "Sync %s (%s): %d cotizaciones, %d barras, %d fundamentales, %d FX en %.1fs",
        run.status,
        run.trigger,
        run.quotes_updated,
        run.bars_written,
        run.fundamentals_updated,
        run.fx_updated,
        run.duration_seconds or 0,
    )
    return run


IntradayScope = Literal["positions", "universe"]


def _held_assets(db: Session) -> list[Asset]:
    ids = universe_service.held_asset_ids(db)
    if not ids:
        return []
    return list(
        db.scalars(select(Asset).where(Asset.id.in_(ids)).order_by(Asset.symbol)).all()
    )


def run_intraday_refresh(
    db: Session, provider: MarketProvider, *, scope: IntradayScope
) -> RefreshReport:
    """Refresco ligero de jornada: cotizaciones + FX, nada más.

    NO crea un `SyncRun`: el indicador de «última sincronización» sigue al job
    completo, y salpicarlo de refrescos parciales lo volvería ilegible. Sí
    reutiliza el troceado en lotes con pausa del pipeline completo, porque el
    rate limit de Yahoo no distingue entre un refresco parcial y uno completo.

    - `scope="positions"`: solo los activos con posición registrada. Un puñado
      de símbolos; es lo que el dashboard de portafolio muestra en vivo.
    - `scope="universe"`: todo el universo de ingesta, para que el ranking de
      oportunidades (`fresh_trailing_pe` usa el precio de ahora) no se quede
      con el precio de la apertura.
    """
    report = RefreshReport()
    assets = (
        _held_assets(db)
        if scope == "positions"
        else universe_service.get_ingestion_universe(db)
    )
    if not assets:
        return report

    service = MarketDataService(db, provider)
    currencies = _base_currencies(db)
    batch_size = max(1, settings.ingestion_batch_size)
    delay = max(0.0, settings.ingestion_batch_delay_seconds)
    chunks = list(_batches(assets, batch_size))

    for index, chunk in enumerate(chunks):
        logger.info(
            "Refresco intradía (%s): lote %d/%d (%d símbolos)",
            scope, index + 1, len(chunks), len(chunk),
        )
        report.merge(service.refresh_quotes_and_fx(chunk, currencies))
        if delay and index < len(chunks) - 1:
            time.sleep(delay)

    logger.info(
        "Refresco intradía (%s): %d cotizaciones, %d FX, %d fallos",
        scope,
        report.quotes_updated,
        report.fx_updated,
        len(set(report.failed_symbols)),
    )
    return report


def _assets_for_symbols(db: Session, symbols: list[str]) -> list[Asset]:
    wanted = {s.strip().upper() for s in symbols if s.strip()}
    if not wanted:
        return []
    return list(
        db.scalars(select(Asset).where(Asset.symbol.in_(wanted)).order_by(Asset.symbol)).all()
    )


def ensure_data_for(
    db: Session, provider: MarketProvider, assets: list[Asset]
) -> RefreshReport:
    """Carga PEREZOSA: trae bajo demanda los datos que falten.

    Es la pieza que hace usable un catálogo grande. Un activo del catálogo no
    se ingesta a diario -serían cientos de llamadas- así que la primera vez que
    se necesita de verdad (al puntuarlo, simularlo o valorarlo) no tiene ni
    precios ni fundamentales. En lugar de mostrarlo vacío hasta la próxima
    sincronización, se piden aquí.

    Solo se pide lo que FALTA: los que ya tienen histórico se saltan sin tocar
    la red. Por eso la comprobación mira barras almacenadas y no el TTL, que
    respondería otra pregunta ("¿está fresco?" en vez de "¿existe?").
    """
    report = RefreshReport()
    if not assets or not settings.enable_lazy_ingestion:
        return report

    from app.repositories import market as market_repo

    known = market_repo.get_last_bar_dates(db, [a.id for a in assets])
    missing = [a for a in assets if a.id not in known]
    if not missing:
        return report

    logger.info("Carga perezosa de %d activos sin histórico", len(missing))
    service = MarketDataService(db, provider)
    currencies = _base_currencies(db)

    batch_size = max(1, settings.ingestion_batch_size)
    chunks = list(_batches(missing, batch_size))
    delay = max(0.0, settings.ingestion_batch_delay_seconds)

    for index, chunk in enumerate(chunks):
        report.merge(service.full_refresh(chunk, currencies))
        if delay and index < len(chunks) - 1:
            time.sleep(delay)

    return report


def prune_history(db: Session, *, retention_days: int | None = None) -> int:
    """Borra barras más antiguas que la retención configurada.

    Desactivado por defecto (`enable_history_pruning`). Borrar histórico es
    irreversible sin volver a descargarlo, y ninguna métrica actual mira más
    atrás de ~452 días hábiles, así que la retención por defecto (1.100 días
    naturales) ya deja holgura de sobra. Se expone para cuando el universo
    crezca lo suficiente como para que el tamaño importe.
    """
    days = retention_days or settings.price_history_retention_days
    cutoff = dt.date.today() - dt.timedelta(days=days)
    result = db.execute(delete(PriceHistory).where(PriceHistory.date < cutoff))
    db.commit()
    deleted = result.rowcount or 0
    if deleted:
        logger.info("Retención: %d barras anteriores a %s eliminadas", deleted, cutoff)
    return deleted


def storage_stats(db: Session) -> dict[str, int | str | None]:
    """Cifras de tamaño del histórico, para vigilar el crecimiento."""
    total = db.scalar(select(func.count()).select_from(PriceHistory)) or 0
    oldest = db.scalar(select(func.min(PriceHistory.date)))
    newest = db.scalar(select(func.max(PriceHistory.date)))
    assets_with_history = (
        db.scalar(select(func.count(func.distinct(PriceHistory.asset_id)))) or 0
    )
    return {
        "bars": total,
        "assets_with_history": assets_with_history,
        "oldest_bar": oldest.isoformat() if oldest else None,
        "newest_bar": newest.isoformat() if newest else None,
        # 95 bytes/fila medidos sobre 302.400 barras con el esquema actual
        # (WITHOUT ROWID, sin source ni fetched_at por fila).
        "estimated_bytes": total * 95,
    }
