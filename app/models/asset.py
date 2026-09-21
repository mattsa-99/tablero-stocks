from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdType, TimestampMixin
from app.db.types import UtcDateTime
from app.models.enums import AssetType, sa_enum

if TYPE_CHECKING:
    from app.models.fixed_income import FixedIncomeTerms
    from app.models.fund import FundProfile
    from app.models.fundamentals import FundamentalSnapshot
    from app.models.market import AssetQuote, PriceHistory
    from app.models.transaction import Transaction


class Asset(Base, TimestampMixin):
    """Catálogo global de instrumentos, compartido por todos los portafolios.

    Es lo que permite que un mismo activo aparezca en varias carteras sin
    duplicar ni una fila de datos de mercado. La relación N:N entre portafolios
    y activos se materializa a través de ``transactions``.
    """

    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint("length(currency) = 3", name="currency_iso"),
        Index("ix_assets_sector", "sector"),
        Index("ix_assets_is_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)

    # Ticker tal y como lo entiende yfinance (AAPL, VWCE.DE, ...).
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    asset_type: Mapped[AssetType] = mapped_column(
        sa_enum(AssetType, "asset_type"), nullable=False, default=AssetType.STOCK
    )

    # Divisa NATIVA de cotización, distinta de la divisa base del portafolio.
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    exchange: Mapped[str | None] = mapped_column(String(30))

    # Categoría Morningstar del fondo, tal como la devuelve el proveedor
    # (`info["category"]`): "High Yield Bond", "Large Blend", "Digital Assets"...
    #
    # Se guarda CRUDA, sin interpretar, por la misma razón que el sector: es un
    # dato verificado del proveedor y la interpretación -a qué clase de activo
    # corresponde- se deriva en `services/asset_class.py`, donde puede cambiar
    # sin migración. Es lo único que distingue un fondo de bonos de uno de
    # acciones sin llamadas extra: viene en la misma respuesta que el resto de
    # los metadatos, y la cobertura medida es del 100% (182 de 182 fondos).
    fund_category: Mapped[str | None] = mapped_column(String(60))

    # CLASE DECLARADA A MANO, para lo que el proveedor no sabe clasificar.
    #
    # Yahoo no cubre la composición de los fondos de la BVC: `GXTESCOL.CL` es
    # un ETF de deuda pública colombiana y llega sin categoría ni perfil, así
    # que se le supondría de acciones y contaría como renta variable amplia en
    # el reparto de la cartera. Justo lo contrario de para qué se compra.
    #
    # Se declara en `app/data/catalog.json`, que es donde ya se declara la
    # pertenencia al universo: un dato verificado por una persona, no una
    # heurística. Por eso NO cuenta como supuesto.
    declared_asset_class: Mapped[str | None] = mapped_column(String(32))

    # Necesarios para el análisis de diversificación de la fase Quant.
    sector: Mapped[str | None] = mapped_column(String(80))
    industry: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(60))

    # False = delisted o ticker no verificado: se deja de refrescar.
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    last_verified_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)

    # Miembro del universo de seguimiento: se ingesta a diario aunque no se
    # posea. Se separa de `is_active` porque son preguntas distintas —"¿existe
    # y cotiza?" frente a "¿lo vigilo?"— y un activo que se vendió entero sigue
    # activo pero puede salir del universo.
    is_universe: Mapped[bool] = mapped_column(nullable=False, default=False, index=True)

    transactions: Mapped[list[Transaction]] = relationship(back_populates="asset")
    prices: Mapped[list[PriceHistory]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", passive_deletes=True
    )
    quote: Mapped[AssetQuote | None] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    fund_profile: Mapped[FundProfile | None] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    fixed_income_terms: Mapped[FixedIncomeTerms | None] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    fundamentals: Mapped[list[FundamentalSnapshot]] = relationship(
        back_populates="asset",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="FundamentalSnapshot.as_of.desc()",
    )

    def __repr__(self) -> str:
        return f"<Asset id={self.id} symbol={self.symbol!r} currency={self.currency}>"
