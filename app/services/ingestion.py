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

        # PRESUPUESTO DE RELOJ. Se comprueba ENTRE lotes, nunca dentro: cortar
        # a mitad de un lote dejaría unos símbolos con precio nuevo y otros con
        # precio viejo dentro de la misma llamada, y eso no se puede declarar.
        comienzo = time.monotonic()
        presupuesto = max(1, settings.sync_budget_minutes) * 60

        for index, chunk in enumerate(chunks):
            transcurrido = time.monotonic() - comienzo
            if transcurrido > presupuesto and index > 0:
                pendientes = sum(len(c) for c in chunks[index:])
                report.note(
                    f"Sincronización cortada por tiempo: {transcurrido / 60:.0f} "
                    f"minutos superan el presupuesto de "
                    f"{settings.sync_budget_minutes}. Quedaron {pendientes} "
                    f"símbolos sin refrescar, que conservan su último dato. "
                    f"Suele significar que la red está caída o muy lenta."
                )
                logger.warning(
                    "Presupuesto agotado tras %d/%d lotes (%.0f min): %d símbolos "
                    "sin refrescar",
                    index, len(chunks), transcurrido / 60, pendientes,
                )
                break

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
        # El resumen de descartes va al FINAL y como una sola línea: así los
        # avisos que importan caben dentro de `MAX_WARNINGS_STORED`.
        resumen = report.summarise_drops()
        if resumen:
            report.note(resumen)
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

        # El devengo de la renta fija directa va ANTES de la foto: es puro
        # cálculo, no sale a la red, y si no se publica aquí esas posiciones
        # quedarían sin valorar en el reparto por clase.
        _refresh_reference_rates(db, report)
        _publish_fixed_income(db, report)
        _capture_ranking_snapshot(db, assets, run, report)

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


def _refresh_reference_rates(db: Session, report: RefreshReport) -> None:
    """Tasas colombianas de Banrep. Otro proveedor, otro fallo posible.

    Va en su propio try porque es una fuente DISTINTA de Yahoo: que Banrep esté
    caído no puede marcar como fallida una sincronización de mercado que sí
    funcionó, ni al revés. Solo importan para la renta fija directa, y sin
    ellas esa ficha lo dice en vez de inventarse una referencia.
    """
    from app.services import reference_rates as rates_service

    if not settings.enable_reference_rates:
        return

    try:
        escritas, avisos = rates_service.refresh(db)
        for aviso in avisos:
            report.note(aviso)
        if escritas:
            logger.info("Tasas de referencia de Banrep: %d filas", escritas)

        # Las emisiones de CDT van en el MISMO try pero tras las de Banrep:
        # son otra fuente más, y que una no responda no puede impedir que la
        # otra se guarde. Por eso se piden en este orden y no al revés: las de
        # Banrep las usa la ficha siempre; estas, solo al evaluar un CDT.
        emisiones, avisos_cdt = rates_service.refresh_cdt_offers(db)
        for aviso in avisos_cdt:
            report.note(aviso)
        if emisiones:
            logger.info("Emisiones de CDT: %d filas", emisiones)
    except Exception:  # noqa: BLE001 - no puede tumbar la sincronización
        db.rollback()
        logger.exception("No se pudieron actualizar las tasas de referencia")


def _publish_fixed_income(db: Session, report: RefreshReport) -> None:
    """Devenga los TES, CDT y FIC cargados a mano. Nunca tumba el pipeline."""
    from app.services import fixed_income as fixed_income_service

    try:
        escritas = fixed_income_service.publish_accruals(db)
        if escritas:
            report.quotes_updated += escritas
            logger.info("Devengo publicado para %d instrumentos de renta fija", escritas)
    except Exception:  # noqa: BLE001 - no puede tumbar la sincronización
        db.rollback()
        logger.exception("No se pudo publicar el devengo de la renta fija")


def _capture_ranking_snapshot(
    db: Session, assets: list[Asset], run: SyncRun, report: RefreshReport
) -> None:
    """Congela el ranking tras sincronizar, como mucho una vez por semana.

    SOLO SI LA SINCRONIZACIÓN FUE LO BASTANTE COMPLETA. Una foto tomada un día
    en que fallaron 420 de 494 símbolos registra los scores de un ranking
    calculado con datos viejos para el 85% del universo, y meses después el
    scorecard la compararía creyendo que era el ranking de ese día. El daño no
    se puede deshacer porque a posteriori una foto mala es indistinguible de
    una buena: por eso se descarta ANTES, y se dice por qué.

    VA AQUÍ, y no en la petición de la vista, por dos razones. La primera es
    que los datos acaban de refrescarse: una foto tomada en mitad de la semana
    mezclaría precios frescos con fundamentales de hace días. La segunda es que
    capturar desde la petición ataría el contenido de la foto a qué estaba
    mirando el usuario -el `limit` y los filtros recortan `opportunities`- y
    eso la invalidaría como registro.

    NUNCA tumba la sincronización. Es un registro para validar más adelante, no
    un dato del que dependa nada de lo que se ve hoy: si falla, se apunta y se
    sigue. Ver `app/models/scorecard.py` para por qué no se puede derivar
    después.
    """
    from app.services import opportunities as opportunity_service
    from app.services import scorecard as scorecard_service

    if len(assets) < settings.opportunity_min_universe:
        return

    if run.status is SyncStatus.FAILED:
        report.note(
            "No se congeló la foto del ranking: la sincronización falló entera."
        )
        return

    caidos = run.symbols_failed or 0
    pedidos = run.symbols_requested or len(assets)
    share = caidos / pedidos if pedidos else 0.0
    if share > settings.scorecard_max_failed_share:
        report.note(
            f"No se congeló la foto del ranking: fallaron {caidos} de {pedidos} "
            f"símbolos ({share:.0%}), por encima del "
            f"{settings.scorecard_max_failed_share:.0%} admisible. Una foto "
            f"sobre datos a medias es peor que ninguna."
        )
        logger.warning(
            "Foto del ranking descartada: %d/%d símbolos caídos", caidos, pedidos
        )
        return

    for portfolio in db.scalars(select(Portfolio)).all():
        try:
            if not scorecard_service.is_capture_due(db, portfolio):
                continue
            scored = opportunity_service._scored_universe(db, portfolio, assets)
            guardadas = scorecard_service.capture(
                db, portfolio, None, rows=list(scored.rows)
            )
            db.commit()
            logger.info(
                "Foto del ranking: %d filas para la cartera %s",
                guardadas, portfolio.id,
            )
        except Exception:  # noqa: BLE001 - nunca puede tumbar la sincronización
            db.rollback()
            logger.exception(
                "No se pudo capturar la foto del ranking de la cartera %s", portfolio.id
            )


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
