from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdType, TimestampMixin

if TYPE_CHECKING:
    from app.models.transaction import Transaction


class Portfolio(Base, TimestampMixin):
    """Contenedor lógico de transacciones.

    La v1 es mono-usuario. Añadir ``user_id`` más adelante es una migración de
    una sola columna, por lo que no se anticipa aquí.
    """

    __tablename__ = "portfolios"
    __table_args__ = (
        CheckConstraint("length(base_currency) = 3", name="base_currency_iso"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(500))

    # Divisa en la que se reportan TODAS las métricas del portafolio.
    # Inmutable tras la creación: cambiarla invalidaría todos los
    # fx_rate_to_base ya congelados en el ledger.
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="COP")

    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)

    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Transaction.executed_at",
    )

    def __repr__(self) -> str:
        return f"<Portfolio id={self.id} name={self.name!r} base={self.base_currency}>"
