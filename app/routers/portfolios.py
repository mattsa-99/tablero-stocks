from __future__ import annotations

import logging

from fastapi import APIRouter, Query, status

from app.core.exceptions import DuplicateError
from app.models import Portfolio
from app.repositories import market as market_repo
from app.repositories import portfolio as portfolio_repo
from app.routers.dependencies import DbSession, MarketService
from app.schemas.portfolio import PortfolioCreate, PortfolioRead, PortfolioUpdate
from app.schemas.position import PortfolioSummary, PositionRead
from app.schemas.simulation import SimulationRequest, SimulationResponse
from app.schemas.transaction import TransactionRead
from app.services import portfolio as portfolio_service
from app.services import simulation as simulation_service
from app.services import transactions as transaction_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])


def _refresh_if_requested(
    db: DbSession, market: MarketService, portfolio: Portfolio, refresh: bool
) -> list[str]:
    """Refresco perezoso de cotizaciones y FX antes de valorar.

    Best-effort por definición: el resumen se puede servir con lo que ya hay en
    la base de datos. Por eso se captura CUALQUIER excepción, no solo
    ProviderError: ni siquiera un bug inesperado en la capa de proveedores debe
    dejar al usuario sin ver su cartera. Se registra con traza para que el
    fallo no pase desapercibido.
    """
    if not refresh:
        return []
    try:
        assets = portfolio_service.get_portfolio_assets(db, portfolio)
        report = market.ensure_fresh_for_portfolio(assets, portfolio.base_currency)
        return report.warnings
    except Exception as exc:
        logger.exception("Fallo al refrescar datos del portafolio %s", portfolio.id)
        db.rollback()
        return [f"No se pudieron refrescar los datos de mercado: {exc}"]


@router.get("", response_model=list[PortfolioRead])
def list_portfolios(db: DbSession, include_inactive: bool = False):
    return portfolio_repo.list_portfolios(db, include_inactive=include_inactive)


@router.post("", response_model=PortfolioRead, status_code=status.HTTP_201_CREATED)
def create_portfolio(payload: PortfolioCreate, db: DbSession):
    existing = [p.name for p in portfolio_repo.list_portfolios(db, include_inactive=True)]
    if payload.name in existing:
        raise DuplicateError(f"Ya existe un portafolio llamado {payload.name!r}")

    portfolio = Portfolio(
        name=payload.name,
        description=payload.description,
        base_currency=payload.base_currency,
    )
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


@router.get("/{portfolio_id}", response_model=PortfolioSummary)
def get_portfolio_summary(
    portfolio_id: int,
    db: DbSession,
    market: MarketService,
    refresh: bool = Query(True, description="Refrescar cotizaciones y FX caducados"),
):
    """Resumen del portafolio con P&L realizado, no realizado y total."""
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    _refresh_if_requested(db, market, portfolio, refresh)
    return portfolio_service.get_summary(db, portfolio)


@router.patch("/{portfolio_id}", response_model=PortfolioRead)
def update_portfolio(portfolio_id: int, payload: PortfolioUpdate, db: DbSession):
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(portfolio, field, value)
    db.commit()
    db.refresh(portfolio)
    return portfolio


@router.get("/{portfolio_id}/positions", response_model=list[PositionRead])
def get_positions(
    portfolio_id: int,
    db: DbSession,
    market: MarketService,
    refresh: bool = Query(True),
):
    """Posiciones actuales, derivadas del ledger y valoradas a precio de mercado."""
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    _refresh_if_requested(db, market, portfolio, refresh)
    return portfolio_service.get_summary(db, portfolio).positions


@router.post("/{portfolio_id}/simulate", response_model=SimulationResponse)
def simulate_trades(portfolio_id: int, payload: SimulationRequest, db: DbSession):
    """Calcula el impacto de operaciones propuestas SIN guardarlas.

    Las transacciones simuladas se construyen como objetos ORM desconectados y
    nunca pasan por `db.add()`. El campo `persisted` de la respuesta es siempre
    false, y un test cuenta las filas antes y después para garantizarlo.

    Una venta por encima de lo disponible se rechaza igual que al ejecutarla:
    un simulador que permite lo imposible no sirve para decidir.
    """
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    return simulation_service.simulate(db, portfolio, payload)


@router.delete("/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_portfolio(portfolio_id: int, db: DbSession):
    """Elimina un portafolio y TODO su historial de transacciones.

    Borrado real, no lógico: el usuario que pulsa «eliminar» espera que
    desaparezca, y un portafolio marcado inactivo que sigue apareciendo en las
    consultas es una fuente de confusión peor que el borrado.

    La cascada la aplica la base de datos (`ondelete="CASCADE"` en
    `transactions.portfolio_id`), no un bucle en Python: así es atómica y no
    puede dejar transacciones huérfanas si algo falla a medias.

    Los ACTIVOS no se tocan. Son catálogo global compartido, y su histórico de
    precios sigue siendo válido para los demás portafolios.

    Es IRREVERSIBLE. La confirmación vive en la interfaz.
    """
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    transactions = len(portfolio_repo.get_transactions(db, portfolio_id))

    db.delete(portfolio)
    db.commit()

    logger.info(
        "Portafolio %s (%r) eliminado con %d transacciones",
        portfolio_id, portfolio.name, transactions,
    )


@router.get("/{portfolio_id}/transactions", response_model=list[TransactionRead])
def get_transactions(portfolio_id: int, db: DbSession, symbol: str | None = None):
    """Historial completo, en orden cronológico."""
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)

    asset_id = None
    if symbol:
        asset = market_repo.get_assets_by_symbols(db, [symbol]).get(symbol.upper())
        if asset is None:
            return []
        asset_id = asset.id

    rows = portfolio_repo.get_transactions(db, portfolio.id, asset_id=asset_id)
    return [transaction_service.to_read(row) for row in rows]
