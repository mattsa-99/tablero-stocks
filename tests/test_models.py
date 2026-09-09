"""Tests de integridad del modelo.

Cada test aquí verifica que la BASE DE DATOS rechaza un estado imposible,
saltándose la validación de Pydantic. Es la última línea de defensa: si estos
pasan, ningún bug de la capa de servicios puede corromper el ledger.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, StatementError

from app.models import (
    Asset,
    AssetQuote,
    DataSyncState,
    FundamentalSnapshot,
    FxRateDaily,
    Portfolio,
    PriceHistory,
    Transaction,
    TransactionType,
)
from tests.conftest import buy

UTC = dt.UTC


# --------------------------------------------------------------------------
# Exactitud monetaria
# --------------------------------------------------------------------------


def test_decimal_round_trip_is_exact(db, portfolio, asset, now):
    """0.1 + 0.2 debe dar exactamente 0.3 tras pasar por la base de datos.

    Con Float en lugar de ExactNumeric este test falla, y ese error se acumula
    en el coste medio recalculado tras cada compra.
    """
    db.add(buy(portfolio, asset, now, qty="0.1", price="0.2", fx="1"))
    db.add(buy(portfolio, asset, now + dt.timedelta(seconds=1), qty="0.2", price="0.1", fx="1"))
    db.commit()
    db.expire_all()

    rows = db.scalars(select(Transaction).order_by(Transaction.id)).all()
    assert rows[0].quantity + rows[1].quantity == Decimal("0.3")
    assert isinstance(rows[0].quantity, Decimal)


def test_cop_magnitudes_fit(db, portfolio, asset, now):
    """Un importe de 500 millones de COP cabe sin pérdida ni DataError.

    Money(20,8) deja 12 dígitos enteros. Verificarlo aquí en vez de
    descubrirlo en producción con un portafolio real.
    """
    amount = Decimal("500000000.12345678")
    db.add(
        Transaction(
            portfolio_id=portfolio.id,
            type=TransactionType.DEPOSIT,
            executed_at=now,
            cash_amount=amount,
            currency="COP",
            fx_rate_to_base=Decimal("1"),
        )
    )
    db.commit()
    db.expire_all()

    stored = db.scalar(select(Transaction))
    assert stored.cash_amount == amount


def test_naive_datetime_is_rejected(db, portfolio, asset):
    """Un datetime sin offset es un error explícito, no una asunción silenciosa.

    SQLAlchemy envuelve el ValueError del TypeDecorator en StatementError.
    """
    naive = dt.datetime(2026, 8, 30, 10, 0, 0)
    db.add(buy(portfolio, asset, naive))
    with pytest.raises(StatementError, match="timezone-aware"):
        db.commit()


def test_local_offset_round_trips_as_utc(db, portfolio, asset):
    """10:00 en Bogotá (-05:00) se almacena como 15:00Z y se relee aware."""
    bogota = dt.timezone(dt.timedelta(hours=-5))
    local = dt.datetime(2026, 8, 30, 10, 0, 0, tzinfo=bogota)
    db.add(buy(portfolio, asset, local))
    db.commit()
    db.expire_all()

    stored = db.scalar(select(Transaction))
    assert stored.executed_at.tzinfo is not None
    assert stored.executed_at == local
    assert stored.executed_at.astimezone(UTC).hour == 15


# --------------------------------------------------------------------------
# CHECK constraints: dominios numéricos
# --------------------------------------------------------------------------


def test_negative_quantity_rejected(db, portfolio, asset, now):
    """El CAST del CHECK es lo que hace que esto funcione.

    Sin CAST, SQLite compara el TEXT '-5' contra el INTEGER 0 por clase de
    tipo, TEXT gana siempre, y este test pasaría por la razón equivocada
    dejando el CHECK inservible.
    """
    db.add(buy(portfolio, asset, now, qty="-5"))
    with pytest.raises(IntegrityError, match="quantity_positive"):
        db.commit()


def test_zero_quantity_rejected(db, portfolio, asset, now):
    db.add(buy(portfolio, asset, now, qty="0"))
    with pytest.raises(IntegrityError, match="quantity_positive"):
        db.commit()


def test_negative_price_rejected(db, portfolio, asset, now):
    db.add(buy(portfolio, asset, now, price="-1"))
    with pytest.raises(IntegrityError, match="price_non_negative"):
        db.commit()


def test_negative_fees_rejected(db, portfolio, asset, now):
    db.add(buy(portfolio, asset, now, fees="-0.01"))
    with pytest.raises(IntegrityError, match="fees_non_negative"):
        db.commit()


def test_zero_fx_rate_rejected(db, portfolio, asset, now):
    db.add(buy(portfolio, asset, now, fx="0"))
    with pytest.raises(IntegrityError, match="fx_positive"):
        db.commit()


# --------------------------------------------------------------------------
# CHECK constraints: forma del payload según el tipo
# --------------------------------------------------------------------------


def test_buy_with_cash_amount_rejected(db, portfolio, asset, now):
    tx = buy(portfolio, asset, now)
    tx.cash_amount = Decimal("100")
    db.add(tx)
    with pytest.raises(IntegrityError, match="trade_payload"):
        db.commit()


def test_buy_without_asset_rejected(db, portfolio, now):
    db.add(
        Transaction(
            portfolio_id=portfolio.id,
            type=TransactionType.BUY,
            executed_at=now,
            quantity=Decimal("10"),
            price=Decimal("150"),
            currency="USD",
            fx_rate_to_base=Decimal("4000"),
        )
    )
    with pytest.raises(IntegrityError, match="trade_payload"):
        db.commit()


def test_deposit_with_asset_rejected(db, portfolio, asset, now):
    db.add(
        Transaction(
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            type=TransactionType.DEPOSIT,
            executed_at=now,
            cash_amount=Decimal("1000000"),
            currency="COP",
            fx_rate_to_base=Decimal("1"),
        )
    )
    with pytest.raises(IntegrityError, match="cash_no_asset"):
        db.commit()


def test_deposit_with_quantity_rejected(db, portfolio, now):
    db.add(
        Transaction(
            portfolio_id=portfolio.id,
            type=TransactionType.DEPOSIT,
            executed_at=now,
            cash_amount=Decimal("1000000"),
            quantity=Decimal("5"),
            currency="COP",
            fx_rate_to_base=Decimal("1"),
        )
    )
    with pytest.raises(IntegrityError, match="cash_payload"):
        db.commit()


def test_dividend_without_asset_rejected(db, portfolio, now):
    db.add(
        Transaction(
            portfolio_id=portfolio.id,
            type=TransactionType.DIVIDEND,
            executed_at=now,
            cash_amount=Decimal("50"),
            currency="USD",
            fx_rate_to_base=Decimal("4000"),
        )
    )
    with pytest.raises(IntegrityError, match="dividend_needs_asset"):
        db.commit()


def test_dividend_with_asset_and_cash_accepted(db, portfolio, asset, now):
    """El camino feliz del dividendo: fees es la retención en la fuente."""
    db.add(
        Transaction(
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            type=TransactionType.DIVIDEND,
            executed_at=now,
            cash_amount=Decimal("50"),
            fees=Decimal("7.5"),
            currency="USD",
            fx_rate_to_base=Decimal("4000"),
        )
    )
    db.commit()
    assert db.scalar(select(Transaction)).cash_amount == Decimal("50")


# --------------------------------------------------------------------------
# Integridad referencial
# --------------------------------------------------------------------------


def test_deleting_portfolio_cascades_transactions(db, portfolio, asset, now):
    db.add(buy(portfolio, asset, now))
    db.commit()
    assert db.scalar(select(Transaction)) is not None

    db.delete(portfolio)
    db.commit()

    assert db.scalar(select(Transaction)) is None
    # El activo sobrevive: es catálogo global compartido.
    assert db.scalar(select(Asset)) is not None


def test_deleting_asset_with_transactions_is_restricted(db, portfolio, asset, now):
    """RESTRICT en acción: un activo con historial nunca se borra.

    Si PRAGMA foreign_keys no estuviera activo, este borrado tendría éxito y
    dejaría el ledger con una FK huérfana sin que nada fallara.
    """
    db.add(buy(portfolio, asset, now))
    db.commit()

    db.delete(asset)
    with pytest.raises(IntegrityError):
        db.commit()


def test_deleting_asset_cascades_market_data(db, asset, now):
    """Los datos de mercado son regenerables: se borran con el activo."""
    db.add(
        PriceHistory(asset_id=asset.id, date=dt.date(2026, 8, 28), close=230.5)
    )
    db.add(AssetQuote(asset_id=asset.id, price=231.0, currency="USD", fetched_at=now))
    db.add(FundamentalSnapshot(asset_id=asset.id, as_of=dt.date(2026, 8, 28), fetched_at=now))
    db.commit()

    db.delete(asset)
    db.commit()

    assert db.scalar(select(PriceHistory)) is None
    assert db.scalar(select(AssetQuote)) is None
    assert db.scalar(select(FundamentalSnapshot)) is None


# --------------------------------------------------------------------------
# Unicidad
# --------------------------------------------------------------------------


def test_duplicate_symbol_rejected(db, asset):
    db.add(Asset(symbol="AAPL", currency="USD"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_duplicate_portfolio_name_rejected(db, portfolio):
    db.add(Portfolio(name="Principal", base_currency="COP"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_duplicate_external_id_rejected(db, portfolio, asset, now):
    """Base de las importaciones idempotentes desde el broker."""
    first = buy(portfolio, asset, now)
    first.external_id = "BRK-001"
    db.add(first)
    db.commit()

    second = buy(portfolio, asset, now)
    second.external_id = "BRK-001"
    db.add(second)
    with pytest.raises(IntegrityError):
        db.commit()


def test_same_external_id_allowed_across_portfolios(db, portfolio, asset, now):
    other = Portfolio(name="Secundario", base_currency="COP")
    db.add(other)
    db.commit()

    for p in (portfolio, other):
        tx = buy(p, asset, now)
        tx.external_id = "BRK-001"
        db.add(tx)
    db.commit()

    assert len(db.scalars(select(Transaction)).all()) == 2


def test_price_history_pk_prevents_duplicate_bars(db, asset, now):
    day = dt.date(2026, 8, 28)
    db.add(PriceHistory(asset_id=asset.id, date=day, close=230.5))
    db.commit()

    db.add(PriceHistory(asset_id=asset.id, date=day, close=999.0))
    with pytest.raises(IntegrityError):
        db.commit()


def test_fx_same_currency_rejected(db, now):
    db.add(
        FxRateDaily(
            base_currency="USD",
            quote_currency="USD",
            date=dt.date(2026, 8, 28),
            rate=Decimal("1"),
            fetched_at=now,
        )
    )
    with pytest.raises(IntegrityError, match="distinct_currencies"):
        db.commit()


# --------------------------------------------------------------------------
# Multi-portafolio y multi-divisa
# --------------------------------------------------------------------------


def test_asset_shared_across_portfolios(db, asset, now):
    """El mismo activo en dos carteras, con una sola fila en el catálogo."""
    a = Portfolio(name="Cartera A", base_currency="COP")
    b = Portfolio(name="Cartera B", base_currency="USD")
    db.add_all([a, b])
    db.commit()

    db.add(buy(a, asset, now, fx="4000"))
    db.add(buy(b, asset, now, fx="1"))
    db.commit()

    assert len(db.scalars(select(Asset)).all()) == 1
    assert len(db.scalars(select(Transaction)).all()) == 2


def test_fx_rate_is_frozen_per_transaction(db, portfolio, asset, now):
    """El tipo de cambio aplicado es un hecho histórico inmutable.

    Corregir fx_rates a posteriori NO debe mover el coste ya registrado; por
    eso está desnormalizado en la transacción.
    """
    db.add(buy(portfolio, asset, now, fx="3800"))
    db.add(
        FxRateDaily(
            base_currency="USD",
            quote_currency="COP",
            date=now.date(),
            rate=Decimal("4200"),
            fetched_at=now,
        )
    )
    db.commit()
    db.expire_all()

    assert db.scalar(select(Transaction)).fx_rate_to_base == Decimal("3800")


# --------------------------------------------------------------------------
# Aislamiento de la caché
# --------------------------------------------------------------------------


def test_wiping_market_data_leaves_ledger_intact(db, portfolio, asset, now):
    """El criterio de calidad más importante del diseño de caché.

    Vaciar TODAS las tablas de mercado y de control debe dejar el sistema en un
    estado válido, recuperable con un refetch, sin perder ni un dato del
    usuario.
    """
    db.add(buy(portfolio, asset, now))
    db.add(PriceHistory(asset_id=asset.id, date=now.date(), close=230.5))
    db.add(AssetQuote(asset_id=asset.id, price=231.0, currency="USD", fetched_at=now))
    db.add(FundamentalSnapshot(asset_id=asset.id, as_of=now.date(), fetched_at=now))
    db.add(
        FxRateDaily(
            base_currency="USD",
            quote_currency="COP",
            date=now.date(),
            rate=Decimal("4000"),
            fetched_at=now,
        )
    )
    db.add(DataSyncState(resource_type="quote", resource_key="AAPL", ttl_seconds=900))
    db.commit()

    for model in (PriceHistory, AssetQuote, FundamentalSnapshot, FxRateDaily, DataSyncState):
        for row in db.scalars(select(model)).all():
            db.delete(row)
    db.commit()

    assert db.scalar(select(Transaction)) is not None
    assert db.scalar(select(Portfolio)) is not None
    assert db.scalar(select(Asset)) is not None
