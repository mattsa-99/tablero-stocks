from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import FxRate, UtcDateTime


class FxRateDaily(Base):
    """Tipo de cambio diario: 1 unidad de `base_currency` = `rate` de `quote_currency`.

    Sin FK a nada: es una tabla de referencia externa, aislada a propósito del
    grafo transaccional. El tipo aplicado a cada operación se congela en
    ``transactions.fx_rate_to_base``; esta tabla sirve para la valoración
    actual e histórica.

    En el contexto de uso (portafolio en COP con activos en USD) el par
    dominante es USD->COP, y la exactitud de esta tabla condiciona el valor de
    TODA la cartera, no el de una posición.
    """

    __tablename__ = "fx_rates"
    __table_args__ = (
        CheckConstraint("base_currency <> quote_currency", name="distinct_currencies"),
        CheckConstraint("CAST(rate AS NUMERIC) > 0", name="rate_positive"),
        CheckConstraint("length(base_currency) = 3", name="base_currency_iso"),
        CheckConstraint("length(quote_currency) = 3", name="quote_currency_iso"),
    )

    base_currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    quote_currency: Mapped[str] = mapped_column(String(3), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)

    rate: Mapped[Decimal] = mapped_column(FxRate, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="yfinance")
    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<FxRateDaily {self.base_currency}/{self.quote_currency} "
            f"{self.date} rate={self.rate}>"
        )
