"""Pipeline de ingesta: sincronización manual, estado y universo."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.core import scheduler
from app.core.config import settings
from app.core.exceptions import SymbolNotFound
from app.models.sync import SyncStatus, SyncTrigger
from app.routers.dependencies import DbSession, ProviderDep
from app.schemas.asset import AssetRead
from app.schemas.common import Symbol
from app.schemas.search import SymbolSearchResponse, SymbolSuggestion
from app.services import ingestion
from app.services import search as search_service
from app.services import universe as universe_service

router = APIRouter(prefix="/api/market-data", tags=["market-data"])


class SyncRunRead(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    trigger: SyncTrigger
    status: SyncStatus
    started_at: dt.datetime
    finished_at: dt.datetime | None
    duration_seconds: float | None

    symbols_requested: int
    symbols_failed: int
    quotes_updated: int
    bars_written: int
    fundamentals_updated: int
    fx_updated: int
    metadata_updated: int
    warnings: str | None


class SyncStatusRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    last_run: SyncRunRead | None
    last_successful_run: SyncRunRead | None
    is_due: bool = Field(description="True si toca sincronizar (o nunca se hizo)")
    next_scheduled_run: str | None
    universe_size: int
    schedule: str
    storage: dict[str, int | str | None]


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbols: list[Symbol] | None = Field(
        default=None, description="Limitar a estos símbolos. Por defecto, todo el universo"
    )
    force: bool = Field(default=False, description="Ignora el TTL (nunca el backoff)")


@router.post("/sync", response_model=SyncRunRead, status_code=status.HTTP_200_OK)
def sync_market_data(db: DbSession, provider: ProviderDep, payload: SyncRequest | None = None):
    """Fuerza una sincronización bajo demanda.

    Es SÍNCRONA a propósito, no una BackgroundTask: el usuario pulsa un botón y
    espera saber qué pasó. Devolver 202 y un id que hay que consultar añade una
    máquina de estados al frontend para un trabajo que, con el universo por
    defecto y la caché templada, tarda segundos.

    Con la caché fría y un universo grande sí puede tardar minutos; para ese
    caso está el planificador diario, que es quien hace el trabajo pesado.
    """
    payload = payload or SyncRequest()
    run = ingestion.run_sync(
        db,
        provider,
        trigger=SyncTrigger.MANUAL,
        force=payload.force,
        symbols=payload.symbols,
    )
    return run


@router.get("/status", response_model=SyncStatusRead)
def get_sync_status(db: DbSession):
    """Estado del pipeline: alimenta el indicador de «última sincronización»."""
    return SyncStatusRead(
        last_run=ingestion.last_run(db),
        last_successful_run=ingestion.last_successful_run(db),
        is_due=ingestion.is_sync_due(db),
        next_scheduled_run=scheduler.next_run_time(),
        universe_size=universe_service.universe_size(db),
        schedule=(
            f"{settings.sync_days} {settings.sync_hour:02d}:{settings.sync_minute:02d} "
            f"{settings.sync_timezone}"
        ),
        storage=ingestion.storage_stats(db),
    )


@router.get("/search", response_model=SymbolSearchResponse)
def search_symbols(
    db: DbSession,
    provider: ProviderDep,
    q: str = Query(..., min_length=1, max_length=60, description="Nombre o ticker parcial"),
    limit: int = Query(8, ge=1, le=25),
):
    """Autocompletado de símbolos: busca por NOMBRE o por ticker parcial.

    Primero el catálogo local -instantáneo y sin red- y después el buscador de
    Yahoo para lo que no se conozca. Un fallo del proveedor NO es un error de
    este endpoint: devuelve las coincidencias locales con un aviso, porque un
    autocompletado que se cae al perder la red es peor que uno que sugiere menos.

    Consultas de menos de 2 caracteres devuelven vacío: con una letra los
    resultados son ruido y cada pulsación cuesta una llamada al proveedor.
    """
    results, warnings = search_service.search_symbols(db, provider, q, limit=limit)
    return SymbolSearchResponse(query=q, results=results, warnings=warnings)


@router.get("/resolve/{symbol}", response_model=SymbolSuggestion)
def resolve_symbol(symbol: str, db: DbSession, provider: ProviderDep):
    """Completa divisa y sector del símbolo elegido. No persiste nada.

    Se llama UNA vez al seleccionar, no una por sugerencia: es lo que permite
    que el formulario rellene divisa y sector solo, sin multiplicar las
    llamadas al proveedor por el número de resultados mostrados.
    """
    suggestion = search_service.resolve_symbol(db, provider, symbol)
    if suggestion is None:
        raise SymbolNotFound(
            f"No se pudo resolver el símbolo {symbol.upper()}: no está en el "
            f"catálogo y el proveedor no lo reconoce."
        )
    return suggestion


@router.get("/universe", response_model=list[AssetRead])
def get_universe(db: DbSession):
    """Activos que el pipeline ingesta a diario.

    Es la unión de los miembros declarados y los que se poseen: comprar algo
    fuera de la lista no puede dejarlo sin precio.
    """
    return universe_service.get_ingestion_universe(db)


@router.post("/universe/{symbol}", response_model=AssetRead)
def add_to_universe(symbol: str, db: DbSession):
    return universe_service.set_membership(db, symbol, True)


@router.delete("/universe/{symbol}", response_model=AssetRead)
def remove_from_universe(symbol: str, db: DbSession):
    """Saca un símbolo del seguimiento diario.

    NO borra el activo ni su histórico: puede tener transacciones asociadas y
    los datos ya descargados siguen siendo válidos.
    """
    return universe_service.set_membership(db, symbol, False)


@router.post("/prune", response_model=dict)
def prune_history(
    db: DbSession,
    retention_days: int | None = Query(
        None, ge=400, description="Días de histórico a conservar"
    ),
):
    """Elimina barras antiguas según la política de retención.

    Requiere al menos 400 días: por debajo se romperían la SMA200 y el momentum
    12-1, que necesitan ~452 días hábiles de ventana.
    """
    deleted = ingestion.prune_history(db, retention_days=retention_days)
    return {"deleted_bars": deleted, "storage": ingestion.storage_stats(db)}
