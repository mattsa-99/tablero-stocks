from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Date, Float, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdType
from app.db.types import BigMoney, UtcDateTime

if TYPE_CHECKING:
    from app.models.asset import Asset

# JSON genérico en SQLite, JSONB indexable en PostgreSQL.
JsonType = JSON().with_variant(JSONB, "postgresql")


class FundamentalSnapshot(Base):
    """Foto de fundamentales en una fecha concreta.

    Historizado (una fila por activo y fecha) en lugar de guardar solo el
    último valor: la fase Quant necesita mirar atrás para normalizar métricas
    frente a su propio histórico, no solo leer el dato de hoy.

    TODOS los ratios son nullable a propósito. yfinance devuelve None con
    frecuencia (P/E de empresas en pérdidas, ROE de ETFs, deuda de financieras)
    y cualquier diseño que los haga obligatorios se rompe el primer día. La
    fase Quant debe tratar el NULL de forma explícita, nunca imputarlo en
    silencio.
    """

    __tablename__ = "fundamental_snapshots"
    __table_args__ = (
        UniqueConstraint("asset_id", "as_of", name="uq_fundamental_snapshots_asset_as_of"),
        Index("ix_fundamental_snapshots_asset_as_of", "asset_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        IdType, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    as_of: Mapped[dt.date] = mapped_column(Date, nullable=False)

    market_cap: Mapped[Decimal | None] = mapped_column(BigMoney)

    # Valoración
    trailing_pe: Mapped[float | None] = mapped_column(Float)
    forward_pe: Mapped[float | None] = mapped_column(Float)
    price_to_book: Mapped[float | None] = mapped_column(Float)
    price_to_sales: Mapped[float | None] = mapped_column(Float)
    ev_to_ebitda: Mapped[float | None] = mapped_column(Float)

    # Rentabilidad y calidad
    profit_margin: Mapped[float | None] = mapped_column(Float)
    return_on_equity: Mapped[float | None] = mapped_column(Float)
    debt_to_equity: Mapped[float | None] = mapped_column(Float)
    revenue_growth: Mapped[float | None] = mapped_column(Float)
    earnings_growth: Mapped[float | None] = mapped_column(Float)
    eps_trailing: Mapped[float | None] = mapped_column(Float)

    # Dividendo y riesgo
    dividend_yield: Mapped[float | None] = mapped_column(Float)
    payout_ratio: Mapped[float | None] = mapped_column(Float)
    beta: Mapped[float | None] = mapped_column(Float)

    # Rango
    fifty_two_week_high: Mapped[float | None] = mapped_column(Float)
    fifty_two_week_low: Mapped[float | None] = mapped_column(Float)

    # Payload íntegro: evita re-descargarlo todo si la fase Quant necesita un
    # campo que hoy no está modelado como columna.
    raw: Mapped[dict[str, Any] | None] = mapped_column(JsonType)

    source: Mapped[str] = mapped_column(String(20), nullable=False, default="yfinance")
    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="fundamentals")

    def __repr__(self) -> str:
        return f"<FundamentalSnapshot asset_id={self.asset_id} as_of={self.as_of}>"
