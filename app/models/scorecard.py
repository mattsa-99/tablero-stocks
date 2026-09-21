"""Foto semanal del ranking: la única forma de validar esto hacia adelante.

POR QUÉ NO SE PUEDE DERIVAR DESPUÉS
===================================
El score de un activo un martes cualquiera depende de tres cosas que NO se
conservan: el precio de ese día, los fundamentales vigentes entonces y el
universo completo contra el que se calculó el percentil. Los fundamentales se
reescriben con cada sincronización -`fundamental_snapshots` guarda una fila por
día, pero solo desde hace tres semanas y con huecos- y el universo cambia.

Así que «¿acertó la calificación A?» no se puede responder mirando atrás. Solo
se puede responder si se empieza a guardar HOY.

LO QUE ESTO NO ES
=================
No es un backtest. Un backtest recorre el pasado; esto espera al futuro. A seis
meses no dirá nada concluyente y a dos años dirá poco: con cinco categorías,
cinco clases y unos cientos de activos, el ruido es enorme. Lo que sí puede
hacer es detectar lo contrario de lo esperado -que los D rindan más que los A-,
que es exactamente la clase de error que una heurística sin validar comete sin
avisar.

Se guarda el PRECIO junto al score, y no es redundante: es lo que permite medir
el rendimiento desde la foto sin depender de que la serie histórica siga
teniendo ese día.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Date, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdType
from app.db.types import UtcDateTime


class RankingSnapshot(Base):
    """Una fila del ranking, congelada en una fecha."""

    __tablename__ = "ranking_snapshots"
    __table_args__ = (
        UniqueConstraint("captured_on", "portfolio_id", "symbol", name="uq_ranking_snapshot"),
        Index("ix_ranking_snapshots_symbol_date", "symbol", "captured_on"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    captured_on: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)

    # La cartera importa aunque el score ya no dependa de ella: el universo sí
    # puede diferir (símbolos vigilados por el diario), y sin esto dos carteras
    # chocarían en la clave única.
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )

    # El SÍMBOLO y no el asset_id: la foto tiene que sobrevivir a que el activo
    # se borre del catálogo, que es justo cuando más interesa saber qué pasó.
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False)

    score: Mapped[float] = mapped_column(Float, nullable=False)
    grade: Mapped[str] = mapped_column(String(16), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    class_size: Mapped[int] = mapped_column(Integer, nullable=False)
    available_signals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Precio en divisa de COTIZACIÓN. Sin convertir a propósito: el rendimiento
    # de la decisión se mide en la divisa del activo, y mezclar el efecto de la
    # divisa aquí haría irreproducible la comparación.
    price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(3))

    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)

    def __repr__(self) -> str:
        return f"<RankingSnapshot {self.captured_on} {self.symbol} {self.grade}>"
