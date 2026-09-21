"""Plan de asignación entre clases de activo: cuánto QUIERES en cada una.

POR QUÉ ES UNA TABLA Y NO UN CÁLCULO
====================================
En este sistema casi todo se deriva del ledger, y la regla por defecto es
«asume que se calcula». Esto es la excepción, y por una razón concreta: el
ledger dice lo que HICISTE, no lo que QUERÍAS. Un 40% en renta variable puede
ser el plan o el resultado de no haber rebalanceado en dos años, y esos dos
casos piden acciones opuestas. No hay forma de distinguirlos mirando las
transacciones.

Es el mismo tipo de dato que el precio de invalidación del diario: una
intención del usuario, no una medición del mercado.

LAS BANDAS NO SON ADORNO
========================
Un objetivo sin tolerancia obliga a rebalancear por cualquier desviación, y
rebalancear cuesta comisiones e impuestos. La banda define cuándo la
desviación deja de ser ruido. El ancho por defecto es 5 puntos, que es un
juicio declarado y no el resultado de una optimización.

ESTO NO EJECUTA NADA. Dice cuánto te has desviado y sugiere dirigir los
APORTES NUEVOS al que más abajo esté, que es la forma de rebalancear sin
vender y sin realizar ganancias.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdType, TimestampMixin
from app.db.types import ExactNumeric

# Porcentaje con dos decimales: 33,33 tiene que poder sumar 100 exacto.
Percent = ExactNumeric(6, 2)

if TYPE_CHECKING:
    from app.models.portfolio import Portfolio


class AllocationTarget(Base, TimestampMixin):
    """Peso objetivo de una clase de activo dentro de una cartera."""

    __tablename__ = "allocation_targets"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "asset_class", name="uq_allocation_portfolio_class"),
        # Los porcentajes son TEXT bajo SQLite (ver `app/db/types.py`), así que
        # el CHECK necesita el CAST o `'-5' > 0` sería verdadero por clase de
        # tipo. Es la misma trampa que ya mordió en `transactions`.
        CheckConstraint(
            "CAST(target_pct AS NUMERIC) >= 0 AND CAST(target_pct AS NUMERIC) <= 100",
            name="target_pct_range",
        ),
        CheckConstraint(
            "CAST(band_pct AS NUMERIC) >= 0 AND CAST(band_pct AS NUMERIC) <= 100",
            name="band_pct_range",
        ),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # El valor de `services.asset_class.AssetClass`, guardado como texto y no
    # como Enum de base de datos: añadir una clase no debe exigir una
    # migración, y la validación vive en el servicio, que es quien conoce el
    # conjunto vigente.
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False)

    # Porcentaje exacto, no float: es un dato del usuario y la suma tiene que
    # poder cuadrar a 100 sin arrastre binario.
    target_pct: Mapped[Decimal] = mapped_column(Percent, nullable=False)
    band_pct: Mapped[Decimal] = mapped_column(
        Percent, nullable=False, default=Decimal("5")
    )

    notes: Mapped[str | None] = mapped_column(String(300))

    portfolio: Mapped[Portfolio] = relationship(back_populates="allocation_targets")

    def __repr__(self) -> str:
        return f"<AllocationTarget {self.asset_class}={self.target_pct}%>"
