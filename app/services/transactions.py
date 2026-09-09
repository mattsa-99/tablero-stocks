"""Servicio de escritura del ledger.

Toda alta o modificación pasa por aquí, nunca directamente por el repositorio:
es donde se resuelven divisa y FX, y donde se valida que el ledger resultante
sea reproducible.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.exceptions import InvalidLedgerOperation, NotFoundError
from app.models import Asset, Portfolio, Transaction, TransactionType
from app.repositories import market as market_repo
from app.repositories import portfolio as portfolio_repo
from app.schemas.transaction import TransactionCreate, TransactionRead, TransactionUpdate
from app.services.pnl import replay_ledger


def _net_cash_flow_base(transaction: Transaction) -> Decimal:
    """Efectivo neto que mueve la operación, en divisa base. Negativo = salida."""
    fx = transaction.fx_rate_to_base
    fees = transaction.fees * fx

    if transaction.type is TransactionType.BUY:
        return -(transaction.quantity * transaction.price * fx + fees)
    if transaction.type is TransactionType.SELL:
        return transaction.quantity * transaction.price * fx - fees
    if transaction.type in (TransactionType.DIVIDEND, TransactionType.DEPOSIT):
        return transaction.cash_amount * fx - fees
    if transaction.type is TransactionType.WITHDRAWAL:
        return -(transaction.cash_amount * fx + fees)
    return Decimal("0")


def to_read(transaction: Transaction) -> TransactionRead:
    """Aplana el ORM al contrato público: símbolo fuera, asset_id dentro."""
    return TransactionRead(
        id=transaction.id,
        portfolio_id=transaction.portfolio_id,
        type=transaction.type,
        executed_at=transaction.executed_at,
        symbol=transaction.asset.symbol if transaction.asset else None,
        asset_name=transaction.asset.name if transaction.asset else None,
        quantity=transaction.quantity,
        price=transaction.price,
        cash_amount=transaction.cash_amount,
        fees=transaction.fees,
        currency=transaction.currency,
        fx_rate_to_base=transaction.fx_rate_to_base,
        net_cash_flow_base=_net_cash_flow_base(transaction),
        notes=transaction.notes,
        external_id=transaction.external_id,
        created_at=transaction.created_at,
    )


def _resolve_fx(
    db: Session, payload: TransactionCreate, currency: str, portfolio: Portfolio
) -> Decimal:
    """Determina el tipo de cambio a CONGELAR en la transacción.

    Prioridad: el valor explícito del usuario, luego la tabla fx_rates en la
    fecha de la operación. Si no hay ninguno y las divisas difieren, se
    rechaza: escribir 1 por defecto produciría un coste base equivocado por un
    factor de ~4000 en una cartera COP con activos en USD, y ese error queda
    congelado para siempre.
    """
    if payload.fx_rate_to_base is not None:
        return payload.fx_rate_to_base

    base = portfolio.base_currency.upper()
    if currency.upper() == base:
        return Decimal("1")

    rate = market_repo.get_fx_rate(
        db, currency.upper(), base, payload.executed_at.date()
    )
    if rate is None:
        raise InvalidLedgerOperation(
            f"No hay tipo de cambio {currency}->{base} para "
            f"{payload.executed_at.date()}. Indica fx_rate_to_base explícitamente o "
            f"ejecuta POST /api/market/refresh."
        )
    return rate


def create_transaction(
    db: Session, portfolio_id: int, payload: TransactionCreate
) -> Transaction:
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)

    asset: Asset | None = None
    if payload.symbol is not None:
        asset = portfolio_repo.get_or_create_asset(db, payload.symbol)

    currency = payload.currency or (asset.currency if asset else portfolio.base_currency)
    fx = _resolve_fx(db, payload, currency, portfolio)

    transaction = Transaction(
        portfolio_id=portfolio.id,
        asset_id=asset.id if asset else None,
        type=payload.type,
        executed_at=payload.executed_at,
        quantity=payload.quantity,
        price=payload.price,
        cash_amount=payload.cash_amount,
        fees=payload.fees,
        currency=currency.upper(),
        fx_rate_to_base=fx,
        notes=payload.notes,
        external_id=payload.external_id,
    )

    # Se reproduce el ledger COMPLETO con la candidata incluida, no solo se
    # compara contra la posición actual: una transacción puede insertarse con
    # fecha pasada, y una compra de hoy no legitima una venta de hace un mes.
    existing = portfolio_repo.get_transactions(db, portfolio.id)
    replay_ledger([*existing, transaction], strict=True)

    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction


def update_transaction(
    db: Session, transaction_id: int, payload: TransactionUpdate
) -> Transaction:
    transaction = portfolio_repo.get_transaction(db, transaction_id)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return transaction

    original = {field: getattr(transaction, field) for field in updates}
    for field, value in updates.items():
        setattr(transaction, field, value)

    siblings = [
        t for t in portfolio_repo.get_transactions(db, transaction.portfolio_id)
        if t.id != transaction.id
    ]
    try:
        replay_ledger([*siblings, transaction], strict=True)
    except InvalidLedgerOperation:
        for field, value in original.items():
            setattr(transaction, field, value)
        db.rollback()
        raise

    db.commit()
    db.refresh(transaction)
    return transaction


def delete_transaction(db: Session, transaction_id: int) -> None:
    """Borra una transacción, siempre que el ledger resultante siga siendo válido.

    Borrar una COMPRA puede dejar descubierta una venta posterior, así que se
    revalida igual que en un alta.
    """
    transaction = portfolio_repo.get_transaction(db, transaction_id)
    remaining = [
        t for t in portfolio_repo.get_transactions(db, transaction.portfolio_id)
        if t.id != transaction.id
    ]
    replay_ledger(remaining, strict=True)

    db.delete(transaction)
    db.commit()


def get_transaction_or_404(db: Session, transaction_id: int) -> Transaction:
    try:
        return portfolio_repo.get_transaction(db, transaction_id)
    except NotFoundError:
        raise
