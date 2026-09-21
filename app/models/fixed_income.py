"""Condiciones de un instrumento de renta fija directa: TES, CDT o FIC.

POR QUÉ UNA TABLA APARTE Y NO COLUMNAS EN `assets`
=================================================
Son ocho campos que solo tienen sentido para un puñado de filas. Metidos en
`assets` estarían nulos en los 735 activos restantes y cualquier consulta
tendría que saber cuándo mirarlos. Aquí la relación es uno a uno y su
presencia ES la declaración de que ese activo es de renta fija directa.

POR QUÉ NO HAY PRECIO DE MERCADO
================================
Ningún proveedor cotiza un CDT: es un contrato entre dos partes. Un TES sí se
negocia, pero su precio lo publican proveedores de precios de pago, y el propio
Banco de la República advierte que su curva cero cupón «es de tipo informativo
y su fin no es la valoración de portafolios».

Así que se valora a COSTO MÁS DEVENGO, y eso hay que declararlo cada vez: es
lo que el instrumento vale si se lleva a vencimiento, no lo que alguien
pagaría hoy por él. Si las tasas suben, un TES vale menos de lo que dice esta
cuenta; si bajan, más. Ver `services/fixed_income.py`.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdType, TimestampMixin
from app.db.types import ExactNumeric
from app.models.enums import RateKind, sa_enum

if TYPE_CHECKING:
    from app.models.asset import Asset

Rate = ExactNumeric(9, 4)


class FixedIncomeTerms(Base, TimestampMixin):
    """Lo que hay que saber de un TES, un CDT o una FIC para poder juzgarlo."""

    __tablename__ = "fixed_income_terms"
    __table_args__ = (
        CheckConstraint(
            "CAST(annual_rate_pct AS NUMERIC) > -100",
            name="annual_rate_sane",
        ),
        CheckConstraint("matures_on > issued_on", name="matures_after_issue"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Quién debe el dinero. En renta fija el emisor ES el riesgo: un CDT de un
    # banco pequeño al 13,5% y uno de Bancolombia al 13,0% no son el mismo
    # instrumento con medio punto de diferencia.
    issuer: Mapped[str] = mapped_column(String(120), nullable=False)

    rate_kind: Mapped[RateKind] = mapped_column(
        sa_enum(RateKind, "rate_kind"), nullable=False, default=RateKind.FIXED
    )
    # Tasa efectiva anual para FIXED; para las indexadas es el SPREAD sobre el
    # índice, en puntos porcentuales (IBR + 2,50 se guarda como 2.50).
    annual_rate_pct: Mapped[Decimal] = mapped_column(Rate, nullable=False)

    issued_on: Mapped[dt.date] = mapped_column(Date, nullable=False)
    matures_on: Mapped[dt.date] = mapped_column(Date, nullable=False)

    # ¿Se puede salir antes? Un CDT normalmente no; un TES sí, con pérdida si
    # las tasas subieron. Es la diferencia entre «tengo 20 millones» y «tendré
    # 20 millones dentro de dos años», y ninguna cifra de la pantalla la dice.
    has_secondary_market: Mapped[bool] = mapped_column(nullable=False, default=False)

    # Retención en la fuente declarada por el usuario, informativa. El tablero
    # NO calcula impuestos: sirve para que la tasa neta que se compara con la
    # inflación sea la que el usuario va a recibir de verdad.
    withholding_pct: Mapped[Decimal | None] = mapped_column(Rate)

    notes: Mapped[str | None] = mapped_column(String(300))

    asset: Mapped[Asset] = relationship(back_populates="fixed_income_terms")

    def __repr__(self) -> str:
        return f"<FixedIncomeTerms {self.issuer} {self.annual_rate_pct}% -> {self.matures_on}>"
