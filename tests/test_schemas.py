"""Tests de los contratos de API.

Verifican que la validación de entrada rechaza lo que no debe llegar nunca al
ledger, y que los schemas de salida no filtran detalles internos del ORM.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.models.enums import TransactionType
from app.schemas import (
    PortfolioCreate,
    PortfolioUpdate,
    TransactionCreate,
    TransactionRead,
)

UTC = dt.UTC
BOGOTA = dt.timezone(dt.timedelta(hours=-5))


def _past(**kwargs) -> dt.datetime:
    return dt.datetime.now(UTC) - dt.timedelta(days=1, **kwargs)


# --------------------------------------------------------------------------
# Portafolio
# --------------------------------------------------------------------------


def test_portfolio_defaults_to_cop():
    assert PortfolioCreate(name="Principal").base_currency == "COP"


def test_currency_is_normalized_to_upper():
    assert PortfolioCreate(name="P", base_currency="usd").base_currency == "USD"


@pytest.mark.parametrize("bad", ["US", "USDD", "12X", ""])
def test_invalid_currency_rejected(bad):
    with pytest.raises(ValidationError, match="ISO 4217"):
        PortfolioCreate(name="P", base_currency=bad)


def test_base_currency_not_updatable():
    """Cambiarla invalidaría todos los fx_rate_to_base ya congelados."""
    with pytest.raises(ValidationError):
        PortfolioUpdate(base_currency="USD")


def test_typo_in_field_name_is_an_error():
    """extra='forbid': un typo debe ser un 422, no un campo ignorado."""
    with pytest.raises(ValidationError):
        PortfolioCreate(name="P", base_currncy="COP")


# --------------------------------------------------------------------------
# Símbolos
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("aapl", "AAPL"), (" msft ", "MSFT"), ("brk-b", "BRK-B"), ("vwce.de", "VWCE.DE")],
)
def test_symbol_normalization(raw, expected):
    tx = TransactionCreate(
        type=TransactionType.BUY,
        executed_at=_past(),
        symbol=raw,
        quantity=Decimal("1"),
        price=Decimal("100"),
    )
    assert tx.symbol == expected


@pytest.mark.parametrize("bad", ["", "AAPL!", "A" * 21, "-AAPL"])
def test_invalid_symbol_rejected(bad):
    with pytest.raises(ValidationError):
        TransactionCreate(
            type=TransactionType.BUY,
            executed_at=_past(),
            symbol=bad,
            quantity=Decimal("1"),
            price=Decimal("100"),
        )


# --------------------------------------------------------------------------
# Fechas
# --------------------------------------------------------------------------


def test_naive_datetime_rejected():
    with pytest.raises(ValidationError, match="zona horaria"):
        TransactionCreate(
            type=TransactionType.BUY,
            executed_at=dt.datetime(2026, 1, 1, 10, 0),
            symbol="AAPL",
            quantity=Decimal("1"),
            price=Decimal("100"),
        )


def test_future_datetime_rejected():
    with pytest.raises(ValidationError, match="futuro"):
        TransactionCreate(
            type=TransactionType.BUY,
            executed_at=dt.datetime.now(UTC) + dt.timedelta(days=1),
            symbol="AAPL",
            quantity=Decimal("1"),
            price=Decimal("100"),
        )


def test_bogota_offset_converted_to_utc():
    """El usuario registra en hora local; el sistema almacena en UTC."""
    tx = TransactionCreate(
        type=TransactionType.BUY,
        executed_at=dt.datetime(2026, 8, 29, 10, 0, tzinfo=BOGOTA),
        symbol="AAPL",
        quantity=Decimal("1"),
        price=Decimal("100"),
    )
    assert tx.executed_at.tzinfo == UTC
    assert tx.executed_at.hour == 15


# --------------------------------------------------------------------------
# Dominios numéricos
# --------------------------------------------------------------------------


@pytest.mark.parametrize("qty", ["0", "-1"])
def test_non_positive_quantity_rejected(qty):
    with pytest.raises(ValidationError):
        TransactionCreate(
            type=TransactionType.BUY,
            executed_at=_past(),
            symbol="AAPL",
            quantity=Decimal(qty),
            price=Decimal("100"),
        )


def test_negative_price_rejected():
    with pytest.raises(ValidationError):
        TransactionCreate(
            type=TransactionType.BUY,
            executed_at=_past(),
            symbol="AAPL",
            quantity=Decimal("1"),
            price=Decimal("-1"),
        )


def test_zero_price_accepted():
    """Precio cero es legítimo: acciones recibidas sin coste."""
    tx = TransactionCreate(
        type=TransactionType.BUY,
        executed_at=_past(),
        symbol="AAPL",
        quantity=Decimal("1"),
        price=Decimal("0"),
    )
    assert tx.price == Decimal("0")


def test_negative_fees_rejected():
    with pytest.raises(ValidationError):
        TransactionCreate(
            type=TransactionType.BUY,
            executed_at=_past(),
            symbol="AAPL",
            quantity=Decimal("1"),
            price=Decimal("100"),
            fees=Decimal("-1"),
        )


# --------------------------------------------------------------------------
# Coherencia del payload según el tipo
# --------------------------------------------------------------------------


def test_buy_requires_symbol_quantity_and_price():
    with pytest.raises(ValidationError, match="requiere"):
        TransactionCreate(type=TransactionType.BUY, executed_at=_past())


def test_buy_rejects_cash_amount():
    with pytest.raises(ValidationError, match="no admite cash_amount"):
        TransactionCreate(
            type=TransactionType.BUY,
            executed_at=_past(),
            symbol="AAPL",
            quantity=Decimal("1"),
            price=Decimal("100"),
            cash_amount=Decimal("100"),
        )


def test_deposit_rejects_symbol():
    with pytest.raises(ValidationError, match="no admite symbol"):
        TransactionCreate(
            type=TransactionType.DEPOSIT,
            executed_at=_past(),
            symbol="AAPL",
            cash_amount=Decimal("1000000"),
        )


def test_deposit_requires_cash_amount():
    with pytest.raises(ValidationError, match="requiere cash_amount"):
        TransactionCreate(type=TransactionType.DEPOSIT, executed_at=_past())


def test_dividend_requires_symbol_and_cash():
    with pytest.raises(ValidationError, match="requiere"):
        TransactionCreate(
            type=TransactionType.DIVIDEND, executed_at=_past(), cash_amount=Decimal("50")
        )


def test_dividend_rejects_quantity():
    with pytest.raises(ValidationError, match="no admite quantity"):
        TransactionCreate(
            type=TransactionType.DIVIDEND,
            executed_at=_past(),
            symbol="AAPL",
            cash_amount=Decimal("50"),
            quantity=Decimal("10"),
        )


@pytest.mark.parametrize("reserved", [TransactionType.FEE, TransactionType.SPLIT])
def test_reserved_types_rejected_in_v1(reserved):
    """Modelados para no bloquear el futuro, no implementados todavía."""
    with pytest.raises(ValidationError, match="no está soportado"):
        TransactionCreate(type=reserved, executed_at=_past(), cash_amount=Decimal("1"))


def test_valid_deposit_accepted():
    tx = TransactionCreate(
        type=TransactionType.DEPOSIT,
        executed_at=_past(),
        cash_amount=Decimal("5000000"),
        currency="COP",
    )
    assert tx.cash_amount == Decimal("5000000")
    assert tx.symbol is None


# --------------------------------------------------------------------------
# Salida
# --------------------------------------------------------------------------


def test_transaction_read_exposes_symbol_not_asset_id():
    """El cliente ve el símbolo; asset_id es un detalle interno del ORM."""
    fields = TransactionRead.model_fields
    assert "symbol" in fields
    assert "asset_id" not in fields
