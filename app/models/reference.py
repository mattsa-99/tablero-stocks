"""Tasas de referencia colombianas: contra qué se juzga un CDT o un TES.

Vive en su propia tabla y no en `fx_rates` ni en `price_history` porque no es
ni un tipo de cambio ni el precio de un activo: es un indicador macro. Mezclarlo
obligaría a que esas tablas admitieran filas sin activo, que es la clase de
concesión que acaba en una columna `tipo` y en consultas que filtran por ella.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdType
from app.db.types import UtcDateTime


class ReferenceRate(Base):
    """Un valor de una serie de referencia en una fecha."""

    __tablename__ = "reference_rates"
    __table_args__ = (
        UniqueConstraint("series", "as_of", name="uq_reference_rate_series_date"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)

    # La clave de `providers/banrep.py::SERIES`: tes_cop_10y, ibr_on, dtf_90d...
    series: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    as_of: Mapped[dt.date] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)

    # Float y no Decimal a propósito: es un indicador publicado con dos o
    # cuatro decimales, no dinero del usuario. La frontera Decimal/float del
    # proyecto separa exactamente por eso.
    unit: Mapped[str] = mapped_column(String(8), nullable=False, default="%")
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="banrep")
    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)

    def __repr__(self) -> str:
        return f"<ReferenceRate {self.series}@{self.as_of}={self.value}>"
