from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdType, TimestampMixin
from app.db.types import Money, UtcDateTime
from app.models.enums import JournalKind, sa_enum

if TYPE_CHECKING:
    from app.models.asset import Asset
    from app.models.portfolio import Portfolio

JsonType = JSON().with_variant(JSONB, "postgresql")


class JournalEntry(Base, TimestampMixin):
    """Una decisión razonada sobre una empresa: la lista de vigilancia y el diario.

    POR QUÉ EXISTE. El error más caro de un principiante no es elegir mal: es
    no saber por qué compró. Sin la razón escrita, al caer el precio no hay
    forma de distinguir «mi idea era mala» de «mi idea sigue intacta y solo se
    movió el precio», y se vende por miedo o se aguanta por orgullo.

    Cada entrada guarda TRES cosas que obligan a pensar antes de actuar:
    la tesis (por qué), la invalidación (qué hecho demostraría que me equivoqué)
    y la fecha de revisión (cuándo vuelvo a mirar). Y una FOTO de lo que decía
    el Tablero ese día, para poder comparar después con lo que dice hoy.

    No toca el ledger: anotar una decisión no es una operación. Que hayas
    escrito «BUY» no crea ninguna transacción, y las posiciones siguen
    derivándose solo de `transactions`.
    """

    __tablename__ = "journal_entries"
    __table_args__ = (
        CheckConstraint("length(trim(thesis)) > 0", name="thesis_not_blank"),
        Index("ix_journal_entries_portfolio_active", "portfolio_id", "is_active"),
        Index("ix_journal_entries_asset", "asset_id"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        IdType, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    # RESTRICT, igual que en las transacciones: un activo con historial
    # (aquí, con decisiones anotadas) nunca se borra.
    asset_id: Mapped[int] = mapped_column(
        IdType, ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )

    kind: Mapped[JournalKind] = mapped_column(
        sa_enum(JournalKind, "journal_kind"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)

    # --- Lo que escribes tú ---
    thesis: Mapped[str] = mapped_column(Text, nullable=False)
    invalidation: Mapped[str | None] = mapped_column(Text)
    # Precio, en la divisa de COTIZACIÓN del activo, por debajo del cual la
    # tesis queda invalidada. Es lo que permite avisar sin que mires cada día.
    invalidation_price: Mapped[Decimal | None] = mapped_column(Money)
    review_date: Mapped[dt.date] = mapped_column(Date, nullable=False)

    # --- Revisión: cuándo miraste de nuevo y qué concluiste ---
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    review_note: Mapped[str | None] = mapped_column(Text)

    # --- Foto del Tablero el día que se anotó ---
    snapshot_price: Mapped[float | None] = mapped_column(Float)
    snapshot_score: Mapped[float | None] = mapped_column(Float)
    snapshot_rank: Mapped[int | None] = mapped_column(Integer)
    snapshot_grade: Mapped[str | None] = mapped_column(String(20))
    snapshot_verdict: Mapped[str | None] = mapped_column(String(10))
    # [{"code": "...", "level": "red"}, ...]: las banderas de aquel día.
    snapshot_flags: Mapped[Any | None] = mapped_column(JsonType)

    portfolio: Mapped[Portfolio] = relationship()
    asset: Mapped[Asset] = relationship()

    def __repr__(self) -> str:
        return f"<JournalEntry id={self.id} {self.kind} asset_id={self.asset_id}>"
