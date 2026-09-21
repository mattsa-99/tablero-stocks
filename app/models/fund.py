"""Perfil de un fondo: qué tiene dentro y cuánto cuesta tenerlo.

POR QUÉ UNA TABLA Y NO COLUMNAS EN `assets`
===========================================
Son siete campos que solo aplican a fondos, y su presencia ES la declaración
de que el proveedor reconoce a ese activo como uno. En `assets` estarían nulos
en las 500 acciones y cualquier consulta tendría que saber cuándo mirarlos.

QUÉ SE GUARDA Y QUÉ NO
======================
Del payload de `funds_data` se descartan dos cosas que se probaron y no
sirven, y dejarlas fuera es parte del contrato de esta tabla:

  - La DURACIÓN. Medida contra lo que esos fondos son por mandato: SJNK -de
    corto plazo- salía con 6,48 y TLT -de 20+ años- con 3,60. No es un factor
    de escala ni otra unidad; no hay patrón que corregir.
  - Los PESOS POR SECTOR. Para SJNK devuelven `communication_services: 1.0`
    en un fondo que es 98,7% bonos.

Lo que sí resultó estable en los 12 fondos probados es lo que está aquí.

EL RATIO DE GASTOS ES EL CAMPO QUE MÁS IMPORTA
==============================================
Es de lo poco en toda esta base de datos que predice rendimiento futuro de
forma fiable, y lo hace en la dirección obvia: se resta todos los años, con
certeza, haya subido o bajado el mercado. Un 0,75% anual frente a un 0,03%
son 72 puntos básicos de diferencia garantizada; ningún factor del score
tiene esa clase de evidencia detrás.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import JSON, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdType
from app.db.types import UtcDateTime

if TYPE_CHECKING:
    from app.models.asset import Asset

JsonType = JSON().with_variant(JSONB, "postgresql")


class FundProfile(Base):
    """Composición, coste y calidad crediticia de un fondo."""

    __tablename__ = "fund_profiles"

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    category: Mapped[str | None] = mapped_column(String(60))
    legal_type: Mapped[str | None] = mapped_column(String(60))

    # Reparto del patrimonio, en fracción. Es el dato DURO de qué hay dentro y
    # el único que no depende de que el proveedor haya categorizado el fondo:
    # por eso `asset_class.classify` lo usa como red antes de suponer nada.
    stock_position: Mapped[float | None] = mapped_column(Float)
    bond_position: Mapped[float | None] = mapped_column(Float)
    cash_position: Mapped[float | None] = mapped_column(Float)
    other_position: Mapped[float | None] = mapped_column(Float)

    # Fracción anual sobre el patrimonio: 0.0003 = 0,03%.
    expense_ratio: Mapped[float | None] = mapped_column(Float)

    # {"bb": 0.5483, "b": 0.3341, ...}. Solo las categorías con peso: Yahoo
    # devuelve las once siempre y las vacías llenarían la ficha de ceros.
    credit_ratings: Mapped[dict | None] = mapped_column(JsonType)

    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)

    asset: Mapped[Asset] = relationship(back_populates="fund_profile")

    @property
    def expense_ratio_pct(self) -> float | None:
        return None if self.expense_ratio is None else self.expense_ratio * 100

    def __repr__(self) -> str:
        return f"<FundProfile asset={self.asset_id} {self.category}>"
