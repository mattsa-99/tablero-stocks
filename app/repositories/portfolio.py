"""Acceso a datos de portafolios, activos y transacciones."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.exceptions import NotFoundError
from app.models import Asset, Portfolio, Transaction


def get_portfolio(db: Session, portfolio_id: int) -> Portfolio:
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise NotFoundError(f"Portafolio {portfolio_id} no encontrado")
    return portfolio


def list_portfolios(db: Session, *, include_inactive: bool = False) -> list[Portfolio]:
    stmt = select(Portfolio).order_by(Portfolio.name)
    if not include_inactive:
        stmt = stmt.where(Portfolio.is_active.is_(True))
    return list(db.scalars(stmt).all())


def get_transactions(
    db: Session, portfolio_id: int, *, asset_id: int | None = None
) -> list[Transaction]:
    """Ledger completo, ordenado cronológicamente.

    Siempre se carga ENTERO para reproducirlo. Filtrar por fecha rompería el
    coste medio: el estado de una posición depende de toda su historia previa.
    """
    stmt = (
        select(Transaction)
        .where(Transaction.portfolio_id == portfolio_id)
        .options(selectinload(Transaction.asset))
        .order_by(Transaction.executed_at.asc(), Transaction.id.asc())
    )
    if asset_id is not None:
        stmt = stmt.where(Transaction.asset_id == asset_id)
    return list(db.scalars(stmt).all())


def get_transaction(db: Session, transaction_id: int) -> Transaction:
    transaction = db.get(Transaction, transaction_id)
    if transaction is None:
        raise NotFoundError(f"Transacción {transaction_id} no encontrada")
    return transaction


def get_or_create_asset(db: Session, symbol: str, *, currency: str = "USD") -> Asset:
    """Resuelve un símbolo a un Asset, dándolo de alta si hace falta.

    El activo nace SIN verificar (`last_verified_at` a None). El enriquecimiento
    desde el proveedor ocurre aparte: una escritura del usuario no debe quedar
    bloqueada por una llamada de red que puede fallar o tardar.
    """
    symbol = symbol.upper()
    asset = db.scalar(select(Asset).where(Asset.symbol == symbol))
    if asset is None:
        asset = Asset(symbol=symbol, currency=currency.upper())
        db.add(asset)
        db.flush()
    return asset
