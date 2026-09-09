from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.models.sync import SyncTrigger
from app.repositories import market as market_repo
from app.routers.dependencies import DbSession, ProviderDep
from app.schemas.asset import AssetRead
from app.services import ingestion

router = APIRouter(prefix="/api/market", tags=["market"])


class RefreshResponse(BaseModel):
    started_at: dt.datetime
    quotes_updated: int
    bars_written: int
    fundamentals_updated: int
    fx_updated: int
    metadata_updated: int
    warnings: list[str]
    failed_symbols: list[str]


@router.post("/refresh", response_model=RefreshResponse, deprecated=True)
def refresh_market_data(
    db: DbSession,
    provider: ProviderDep,
    force: bool = Query(False, description="Ignora el TTL (nunca el backoff)"),
):
    """DEPRECADO: usa `POST /api/market-data/sync`.

    Se mantiene por compatibilidad pero ahora DELEGA en el pipeline de ingesta
    en lugar de refrescar por su cuenta. Sin esa delegación quedaba un fallo
    confuso: refrescar por aquí actualizaba los datos pero no registraba un
    `SyncRun`, así que el indicador de «última sincronización» de la interfaz
    seguía diciendo «nunca» justo después de haber traído datos frescos.
    """
    started = dt.datetime.now(dt.UTC)
    run = ingestion.run_sync(db, provider, trigger=SyncTrigger.MANUAL, force=force)

    return RefreshResponse(
        started_at=started,
        quotes_updated=run.quotes_updated,
        bars_written=run.bars_written,
        fundamentals_updated=run.fundamentals_updated,
        fx_updated=run.fx_updated,
        metadata_updated=run.metadata_updated,
        warnings=run.warnings.split("\n") if run.warnings else [],
        failed_symbols=[],
    )


@router.get("/assets", response_model=list[AssetRead])
def list_assets(db: DbSession):
    """Catálogo de activos conocidos. Es el universo base de Oportunidades."""
    return market_repo.get_active_assets(db)
