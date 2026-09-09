from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Self

from pydantic import Field, field_validator, model_validator

from app.models.enums import TransactionType
from app.schemas.common import (
    CurrencyCode,
    NonNegativeMoney,
    ORMModel,
    PositiveMoney,
    PositiveQuantity,
    PositiveRate,
    StrictModel,
    Symbol,
)


def _require_aware_past(value: dt.datetime) -> dt.datetime:
    """El offset es obligatorio.

    El usuario registra operaciones pensando en hora local (Colombia, -05:00).
    Aceptar un datetime naive y asumir UTC desplazaría la transacción cinco
    horas, lo que puede cambiar el día de la operación y con él el orden del
    replay.
    """
    if value.tzinfo is None:
        raise ValueError(
            "executed_at debe incluir zona horaria (ej. 2026-08-30T10:00:00-05:00)"
        )
    value = value.astimezone(dt.UTC)
    if value > dt.datetime.now(dt.UTC):
        raise ValueError("executed_at no puede estar en el futuro")
    return value


class TransactionCreate(StrictModel):
    """Alta de un evento en el ledger.

    La API habla de SÍMBOLOS, no de ids de catálogo: el service resuelve o da
    de alta el Asset correspondiente.
    """

    type: TransactionType
    executed_at: dt.datetime
    symbol: Symbol | None = None

    quantity: PositiveQuantity | None = None
    price: NonNegativeMoney | None = None
    cash_amount: PositiveMoney | None = None
    fees: NonNegativeMoney = Decimal("0")

    # Si se omiten, el service los resuelve: `currency` desde el activo y
    # `fx_rate_to_base` desde fx_rates en la fecha de la operación.
    currency: CurrencyCode | None = None
    fx_rate_to_base: PositiveRate | None = None

    notes: str | None = Field(default=None, max_length=500)
    external_id: str | None = Field(default=None, max_length=100)

    _validate_executed_at = field_validator("executed_at")(_require_aware_past)

    @model_validator(mode="after")
    def _validate_payload_by_type(self) -> Self:
        if not self.type.is_supported:
            raise ValueError(f"El tipo {self.type} no está soportado en esta versión")

        if self.type.is_trade:
            missing = [
                field
                for field, value in (
                    ("symbol", self.symbol),
                    ("quantity", self.quantity),
                    ("price", self.price),
                )
                if value is None
            ]
            if missing:
                raise ValueError(f"{self.type} requiere: {', '.join(missing)}")
            if self.cash_amount is not None:
                raise ValueError(f"{self.type} no admite cash_amount")

        elif self.type is TransactionType.DIVIDEND:
            if self.symbol is None:
                raise ValueError("DIVIDEND requiere symbol")
            if self.cash_amount is None:
                raise ValueError("DIVIDEND requiere cash_amount (importe bruto)")
            if self.quantity is not None or self.price is not None:
                raise ValueError("DIVIDEND no admite quantity ni price")

        else:  # DEPOSIT / WITHDRAWAL
            if self.symbol is not None:
                raise ValueError(f"{self.type} no admite symbol")
            if self.cash_amount is None:
                raise ValueError(f"{self.type} requiere cash_amount")
            if self.quantity is not None or self.price is not None:
                raise ValueError(f"{self.type} no admite quantity ni price")

        return self


class TransactionUpdate(StrictModel):
    """Corrección de una transacción ya registrada.

    ``type`` y ``symbol`` son INMUTABLES: convertir un BUY en un SELL debe
    hacerse borrando y recreando, para que el ledger no pueda reinterpretarse
    retroactivamente. Cualquier cambio aquí obliga a revalidar el ledger
    completo del activo, porque puede convertir una venta pasada en descubierta.
    """

    executed_at: dt.datetime | None = None
    quantity: PositiveQuantity | None = None
    price: NonNegativeMoney | None = None
    cash_amount: PositiveMoney | None = None
    fees: NonNegativeMoney | None = None
    fx_rate_to_base: PositiveRate | None = None
    notes: str | None = Field(default=None, max_length=500)

    _validate_executed_at = field_validator("executed_at")(_require_aware_past)


class TransactionRead(ORMModel):
    """Transacción tal y como la ve el cliente.

    ``symbol``, ``asset_name`` y ``net_cash_flow_base`` no son atributos del
    ORM: los rellena el service. El cliente nunca ve ``asset_id``.
    """

    id: int
    portfolio_id: int
    type: TransactionType
    executed_at: dt.datetime

    symbol: str | None = None
    asset_name: str | None = None

    quantity: Decimal | None
    price: Decimal | None
    cash_amount: Decimal | None
    fees: Decimal
    currency: str
    fx_rate_to_base: Decimal

    # Efectivo neto que mueve la operación, en divisa base.
    # Negativo = salida de caja (BUY, WITHDRAWAL).
    net_cash_flow_base: Decimal | None = None

    notes: str | None
    external_id: str | None
    created_at: dt.datetime
