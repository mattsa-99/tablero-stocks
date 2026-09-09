from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.enums import AssetType


class PositionRead(BaseModel):
    """Posición abierta. 100% derivada del ledger y del precio actual.

    No deriva de ningún modelo ORM porque no existe ninguna tabla `positions`:
    esto es el resultado de un cálculo, no la lectura de una fila.

    Los campos de mercado y los calculados son Optional a propósito. Si no hay
    precio, valen None y el símbolo aparece en `positions_without_price` del
    resumen. NUNCA se sustituye por 0: mostraría una pérdida del 100%
    inexistente.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str | None
    asset_type: AssetType
    currency: str  # divisa nativa del activo
    sector: str | None
    # Cubo usado para medir diversificación: sector GICS para acciones, clase
    # de activo para ETFs amplios, renta fija y materias primas. Sin esto, un
    # ETF de índice contaba como "sector desconocido".
    exposure_bucket: str = "Desconocido"

    # --- Derivado del ledger (exacto, en divisa base) ---
    quantity: Decimal
    average_cost: Decimal  # por título
    cost_basis: Decimal  # quantity * average_cost
    total_invested: Decimal  # capital total desplegado, incluye lo ya vendido
    realized_pnl: Decimal
    dividend_income: Decimal  # neto de retenciones

    # --- Mercado (puede faltar o estar obsoleto) ---
    current_price: Decimal | None  # en divisa nativa
    price_as_of: dt.datetime | None
    is_stale: bool
    fx_rate_to_base: Decimal  # tipo de cambio actual, no el histórico

    # --- Calculado ---
    market_value: Decimal | None
    unrealized_pnl: Decimal | None
    unrealized_return_pct: Decimal | None
    day_change_pct: Decimal | None
    weight_pct: Decimal | None


class PortfolioSummary(BaseModel):
    """Resumen agregado. Todos los importes en `base_currency`."""

    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    name: str
    base_currency: str
    as_of: dt.datetime

    total_cost: Decimal  # coste de las posiciones abiertas
    market_value: Decimal | None  # valor de mercado de las posiciones
    cash_balance: Decimal  # derivada del ledger, puede ser negativa
    total_value: Decimal | None  # market_value + cash_balance
    net_invested: Decimal  # depósitos - retiradas

    realized_pnl: Decimal
    unrealized_pnl: Decimal | None
    dividend_income: Decimal
    total_pnl: Decimal | None
    total_return_pct: Decimal | None

    positions: list[PositionRead]

    # Transparencia sobre la calidad del dato: el frontend debe poder avisar
    # en vez de presentar cifras incompletas como si fueran definitivas.
    positions_without_price: list[str]
    has_stale_prices: bool
