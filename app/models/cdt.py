"""Tasas de CDT pactadas por banco y plazo, de la Superintendencia Financiera.

POR QUÉ SE GUARDA EN VEZ DE PEDIRSE AL VUELO
============================================
La ficha de un CDT es una PETICIÓN HTTP, y en este sistema ninguna petición
espera a la red. La alternativa -consultarlo al abrir la ficha- metería una
llamada a datos.gov.co dentro del tiempo de respuesta, que es exactamente lo
que se quitó del motor de oportunidades.

Se refresca junto a las tasas de Banrep, una vez por sincronización.

QUÉ SIGNIFICA `rate_pct`
========================
La tasa efectivamente PACTADA, no la de la vitrina. Suele ser mejor dato que
el anuncio -nadie publica el descuento que hizo por un depósito grande- pero
no es lo que le van a ofrecer al usuario por diez millones, y la ficha lo
declara cada vez.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, Float, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdType
from app.db.types import UtcDateTime


class CdtOffer(Base):
    """Última emisión conocida de una entidad a un plazo."""

    __tablename__ = "cdt_offers"
    __table_args__ = (
        UniqueConstraint("entity", "term_label", name="uq_cdt_offer_entity_term"),
        Index("ix_cdt_offers_term", "term_label"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)

    entity: Mapped[str] = mapped_column(String(120), nullable=False)
    # El nombre del plazo tal y como lo publica la fuente: no hay campo
    # numérico de días, así que la cadena ES la clave. `providers/datos_gov.py`
    # la traduce a un rango.
    term_label: Mapped[str] = mapped_column(String(60), nullable=False)

    rate_pct: Mapped[float] = mapped_column(Float, nullable=False)
    amount: Mapped[float | None] = mapped_column(Float)

    # Fecha del CORTE, no de la descarga. Una tasa de hace una semana sigue
    # orientando; presentarla como de hoy, no.
    as_of: Mapped[dt.date] = mapped_column(Date, nullable=False)
    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)

    def __repr__(self) -> str:
        return f"<CdtOffer {self.entity} {self.term_label} {self.rate_pct}%>"
