from __future__ import annotations

import datetime as dt

from pydantic import Field

from app.schemas.common import CurrencyCode, ORMModel, StrictModel


class PortfolioCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    base_currency: CurrencyCode = "COP"


class PortfolioUpdate(StrictModel):
    """PATCH parcial.

    ``base_currency`` NO es actualizable: cambiarla invalidaría todos los
    ``fx_rate_to_base`` ya congelados en el ledger y el coste histórico dejaría
    de tener sentido.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class PortfolioRead(ORMModel):
    id: int
    name: str
    description: str | None
    base_currency: str
    is_active: bool
    created_at: dt.datetime
    updated_at: dt.datetime
