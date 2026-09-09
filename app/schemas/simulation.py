from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import TransactionType
from app.schemas.common import (
    CurrencyCode,
    NonNegativeMoney,
    PositiveMoney,
    PositiveQuantity,
    PositiveRate,
    StrictModel,
    Symbol,
)


class SimulatedTrade(StrictModel):
    """Una operación propuesta. Mismas reglas que una real, sin fecha.

    No lleva `executed_at`: una simulación es siempre "si lo hiciera AHORA".
    Permitir una fecha pasada abriría la puerta a simular un histórico
    alternativo, que es otra funcionalidad distinta y mucho más cara.
    """

    type: TransactionType
    symbol: Symbol | None = None
    quantity: PositiveQuantity | None = None
    price: NonNegativeMoney | None = None
    cash_amount: PositiveMoney | None = None
    fees: NonNegativeMoney = Decimal("0")
    currency: CurrencyCode | None = None
    fx_rate_to_base: PositiveRate | None = None

    # Solo para símbolos que aún no están en el catálogo: permite calcular su
    # efecto real sobre la diversificación en lugar de agruparlo en
    # «Desconocido».
    sector: str | None = Field(
        default=None,
        max_length=80,
        description="Sector del activo si aún no está en el catálogo",
    )

    @model_validator(mode="after")
    def _validate_payload(self) -> Self:
        if not self.type.is_supported:
            raise ValueError(f"El tipo {self.type} no se puede simular")

        if self.type.is_trade:
            missing = [
                name
                for name, value in (
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
            if self.symbol is None or self.cash_amount is None:
                raise ValueError("DIVIDEND requiere symbol y cash_amount")
        else:  # DEPOSIT / WITHDRAWAL
            if self.symbol is not None:
                raise ValueError(f"{self.type} no admite symbol")
            if self.cash_amount is None:
                raise ValueError(f"{self.type} requiere cash_amount")
        return self


class SimulationRequest(StrictModel):
    trades: list[SimulatedTrade] = Field(min_length=1, max_length=20)


class SectorExposure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sector: str
    value: Decimal
    weight_pct: Decimal


class PortfolioSnapshot(BaseModel):
    """Foto del portafolio: la misma forma para el estado actual y el simulado.

    Compartir el schema es deliberado: garantiza que se comparan magnitudes
    homólogas y que el frontend puede renderizar ambos con el mismo componente.
    """

    model_config = ConfigDict(extra="forbid")

    base_currency: str
    market_value: Decimal
    cost_basis: Decimal
    cash_balance: Decimal
    total_value: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    dividend_income: Decimal
    total_pnl: Decimal
    total_return_pct: Decimal | None

    position_count: int
    sector_exposures: list[SectorExposure]

    diversification_index: Decimal | None = Field(
        default=None,
        description=(
            "100·(1−HHI) sobre pesos por sector. Escala RELATIVA para comparar "
            "antes/después de la misma cartera; con 11 sectores el techo real "
            "es ~90,9, no 100."
        ),
    )
    effective_sectors: Decimal | None = Field(
        default=None, description="1/HHI: sectores equivalentes con peso igual"
    )
    effective_positions: Decimal | None = Field(
        default=None, description="1/HHI sobre pesos por posición"
    )
    largest_position_pct: Decimal

    positions_without_price: list[str]


class SimulationDeltas(BaseModel):
    """Diferencia simulado − actual. Lo que el usuario mira primero."""

    model_config = ConfigDict(extra="forbid")

    market_value: Decimal | None
    cost_basis: Decimal | None
    cash_balance: Decimal | None
    total_value: Decimal | None
    realized_pnl: Decimal | None
    unrealized_pnl: Decimal | None
    total_pnl: Decimal | None
    total_return_pct: Decimal | None
    position_count: int
    diversification_index: Decimal | None
    effective_sectors: Decimal | None
    effective_positions: Decimal | None
    largest_position_pct: Decimal | None

    sector_weight_shifts: dict[str, Decimal] = Field(
        default_factory=dict,
        description="Sector -> variación en puntos porcentuales. Solo los que cambian",
    )


class SimulationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    base_currency: str
    as_of: dt.datetime

    trades: list[SimulatedTrade]
    current_state: PortfolioSnapshot
    simulated_state: PortfolioSnapshot
    deltas: SimulationDeltas

    warnings: list[str] = Field(default_factory=list)
    persisted: bool = Field(
        default=False,
        description="Siempre false: la simulación NUNCA escribe en la base de datos",
    )
