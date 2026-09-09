from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Date, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdType
from app.db.types import UtcDateTime

if TYPE_CHECKING:
    from app.models.asset import Asset


class PriceHistory(Base):
    """OHLCV diario. La tabla que más crece con diferencia.

    Float y no Decimal a propósito: son estimaciones de terceros con 2-4
    decimales significativos, se ingestan por cientos de miles de filas y la
    fase Quant necesita agregarlas en SQL. La frontera con la aritmética
    contable está en la valoración, donde se convierten con Decimal(str(precio)).

    DECISIONES DE ALMACENAMIENTO (medidas sobre 302.400 barras = 120 activos x
    10 años, no estimadas):

    - ``WITHOUT ROWID``: la tabla ES el B-tree agrupado por (asset_id, date),
      en lugar de un heap con rowid más un índice PK aparte. Elimina esa copia
      completa del índice y convierte el barrido por activo en secuencial.

    - Sin columnas ``source`` ni ``fetched_at`` POR FILA. Guardar la cadena
      "yfinance" y un timestamp ISO en cada barra repetía ~64 bytes por fila
      para un dato que pertenece a la INGESTA, no a la barra. La procedencia
      vive ahora en `sync_runs`.

    Resultado combinado: 159 -> 95 bytes por fila (-40%), 48,0 -> 28,7 MB en ese
    volumen, con inserción y barrido también más rápidos. Se descartó además
    almacenar la fecha como entero ordinal: ahorraba otros 17 B/fila pero
    costaba perder la legibilidad y las funciones de fecha de SQL.
    """

    __tablename__ = "price_history"
    __table_args__ = (
        # Índice para los cortes transversales (valorar todo a fecha X). El
        # acceso por activo NO lo necesita: la PK agrupada ya lo cubre.
        Index("ix_price_history_date", "date"),
        {"sqlite_with_rowid": False},
    )

    asset_id: Mapped[int] = mapped_column(
        IdType, ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)

    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    adj_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(BigInteger)

    asset: Mapped[Asset] = relationship(back_populates="prices")

    def __repr__(self) -> str:
        return f"<PriceHistory asset_id={self.asset_id} {self.date} close={self.close}>"


class AssetQuote(Base):
    """Último precio conocido, 1:1 con Asset.

    Tabla separada del catálogo porque el catálogo es casi inmutable y el
    precio cambia cada minuto: mezclarlos obligaría a reescribir filas de
    catálogo constantemente y borraría la frontera entre dato estable y dato
    volátil de mercado.
    """

    __tablename__ = "asset_quotes"

    asset_id: Mapped[int] = mapped_column(
        IdType, ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True
    )
    price: Mapped[float] = mapped_column(Float, nullable=False)
    previous_close: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Hora del dato según el mercado, distinta de cuándo lo pedimos nosotros.
    quote_time: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    fetched_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)

    # True si el último intento de refresco falló: se sirve el valor anterior
    # y el frontend lo señaliza, en vez de devolver un error.
    is_stale: Mapped[bool] = mapped_column(nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="yfinance")

    asset: Mapped[Asset] = relationship(back_populates="quote")

    def __repr__(self) -> str:
        return f"<AssetQuote asset_id={self.asset_id} price={self.price} stale={self.is_stale}>"
