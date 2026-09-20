"""Esquemas de la curva de rendimiento."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ValuePointRead(BaseModel):
    """La cartera en un día, en divisa base."""

    model_config = ConfigDict(extra="forbid")

    date: dt.date
    market_value: Decimal
    cash: Decimal
    total_value: Decimal
    net_invested: Decimal = Field(
        description=(
            "Capital neto aportado hasta ese día. Es la línea contra la que se "
            "lee la curva: por encima se gana, por debajo se pierde"
        )
    )
    benchmark_value: Decimal | None = Field(
        default=None,
        description="Valor de la MISMA aportación puesta en el índice el mismo día",
    )


class PerformanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    base_currency: str
    as_of: dt.datetime
    points: list[ValuePointRead]
    benchmark_symbol: str | None = None

    xirr_pct: float | None = Field(
        default=None,
        description=(
            "Retorno anualizado ponderado por dinero. None si el histórico es "
            "más corto que un trimestre: anualizar un tramo corto multiplica "
            "el ruido"
        ),
    )
    benchmark_xirr_pct: float | None = None

    warnings: list[str] = Field(default_factory=list)
