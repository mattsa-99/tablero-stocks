from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdType, TimestampMixin
from app.db.types import FxRate, Money, Quantity, UtcDateTime
from app.models.enums import TransactionType, sa_enum

if TYPE_CHECKING:
    from app.models.asset import Asset
    from app.models.portfolio import Portfolio


class Transaction(Base, TimestampMixin):
    """Ledger de eventos. Única fuente de verdad del portafolio.

    Posiciones, coste medio, caja y P&L se DERIVAN reproduciendo estas filas en
    orden cronológico. No existe ningún estado agregado persistido que pueda
    desincronizarse del histórico.

    Tabla única con discriminador ``type`` en lugar de una tabla por tipo de
    evento: así el orden cronológico de todos los eventos vive en un solo
    índice y el replay es un ORDER BY, no un merge de tres tablas. El coste son
    columnas nullable, acotado por los CHECK condicionales de abajo.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        # --- Dominios numéricos ---
        # El CAST es imprescindible: en SQLite los importes se guardan como TEXT
        # (ver ExactNumeric) y '-5' > 0 es VERDADERO, porque SQLite compara TEXT
        # contra INTEGER por clase de tipo, no por valor. Sin el CAST estos
        # CHECK no protegen de nada. El CAST es válido en SQLite y PostgreSQL.
        CheckConstraint(
            "quantity IS NULL OR CAST(quantity AS NUMERIC) > 0",
            name="quantity_positive",
        ),
        CheckConstraint(
            "price IS NULL OR CAST(price AS NUMERIC) >= 0",
            name="price_non_negative",
        ),
        CheckConstraint(
            "cash_amount IS NULL OR CAST(cash_amount AS NUMERIC) > 0",
            name="cash_positive",
        ),
        CheckConstraint("CAST(fees AS NUMERIC) >= 0", name="fees_non_negative"),
        CheckConstraint("CAST(fx_rate_to_base AS NUMERIC) > 0", name="fx_positive"),
        # --- Forma del payload según el tipo de evento ---
        CheckConstraint(
            "type NOT IN ('BUY','SELL') OR ("
            "  asset_id IS NOT NULL AND quantity IS NOT NULL"
            "  AND price IS NOT NULL AND cash_amount IS NULL)",
            name="trade_payload",
        ),
        CheckConstraint(
            "type NOT IN ('DIVIDEND','DEPOSIT','WITHDRAWAL') OR ("
            "  cash_amount IS NOT NULL AND quantity IS NULL AND price IS NULL)",
            name="cash_payload",
        ),
        CheckConstraint(
            "type NOT IN ('DEPOSIT','WITHDRAWAL') OR asset_id IS NULL",
            name="cash_no_asset",
        ),
        CheckConstraint(
            "type <> 'DIVIDEND' OR asset_id IS NOT NULL",
            name="dividend_needs_asset",
        ),
        CheckConstraint("length(currency) = 3", name="currency_iso"),
        # Importaciones idempotentes desde el broker.
        UniqueConstraint("portfolio_id", "external_id", name="uq_transactions_external"),
        Index("ix_transactions_portfolio_executed", "portfolio_id", "executed_at"),
        Index(
            "ix_transactions_portfolio_asset_executed",
            "portfolio_id",
            "asset_id",
            "executed_at",
        ),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        IdType, ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    # NULL solo en movimientos de caja puros (DEPOSIT / WITHDRAWAL).
    # RESTRICT: un activo con historial nunca se borra, se marca is_active=False.
    asset_id: Mapped[int | None] = mapped_column(
        IdType, ForeignKey("assets.id", ondelete="RESTRICT")
    )

    type: Mapped[TransactionType] = mapped_column(
        sa_enum(TransactionType, "transaction_type"), nullable=False
    )
    # Define el orden del replay. Se desempata por id para que sea determinista.
    executed_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)

    # Solo BUY/SELL. Siempre positivos: el signo lo aporta `type`.
    quantity: Mapped[Decimal | None] = mapped_column(Quantity)
    price: Mapped[Decimal | None] = mapped_column(Money)
    # Solo DIVIDEND/DEPOSIT/WITHDRAWAL. Importe bruto positivo.
    cash_amount: Mapped[Decimal | None] = mapped_column(Money)
    # Comisiones de broker Y retenciones en la fuente (dividendos).
    fees: Mapped[Decimal] = mapped_column(Money, nullable=False, default=Decimal("0"))

    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Tipo de cambio APLICADO en el momento de la operación, hacia la divisa
    # base del portafolio. Se desnormaliza a propósito respecto de fx_rates:
    # es un hecho histórico inmutable. Sin esta columna, el coste base ya
    # registrado cambiaría cada vez que se corrigen o rellenan los tipos de
    # cambio, y el P&L histórico dejaría de ser reproducible.
    fx_rate_to_base: Mapped[Decimal] = mapped_column(
        FxRate, nullable=False, default=Decimal("1")
    )

    notes: Mapped[str | None] = mapped_column(String(500))
    external_id: Mapped[str | None] = mapped_column(String(100))

    portfolio: Mapped[Portfolio] = relationship(back_populates="transactions")
    asset: Mapped[Asset | None] = relationship(back_populates="transactions")

    def __repr__(self) -> str:
        return (
            f"<Transaction id={self.id} {self.type} "
            f"asset_id={self.asset_id} at={self.executed_at.isoformat()}>"
        )
