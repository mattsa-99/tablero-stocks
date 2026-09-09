from __future__ import annotations

import datetime as dt

from sqlalchemy import BigInteger, Integer, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import UtcDateTime

# Sin naming_convention, SQLite genera constraints anónimos que Alembic no
# puede alterar ni portar a PostgreSQL. No es cosmético: es lo que hace que
# las migraciones futuras sean posibles.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# INTEGER en SQLite, BIGINT en PostgreSQL: la PK queda dimensionada
# correctamente en cada motor sin tocar el modelo.
IdType = Integer().with_variant(BigInteger, "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        UtcDateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )
