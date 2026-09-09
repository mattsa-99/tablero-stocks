from __future__ import annotations

import datetime as dt
from enum import StrEnum

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdType
from app.db.types import UtcDateTime
from app.models.enums import sa_enum


class SyncTrigger(StrEnum):
    SCHEDULED = "SCHEDULED"
    MANUAL = "MANUAL"
    STARTUP_CATCHUP = "STARTUP_CATCHUP"


class SyncStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class DataSyncState(Base):
    """Control de TTL, backoff y rate limiting de los proveedores externos.

    Toda la metadata de caché vive aquí y solo aquí. Ninguna tabla
    transaccional tiene columnas de caché: vaciar por completo esta tabla y las
    de mercado debe dejar el sistema en un estado válido, recuperable con un
    refetch, sin perder ni un dato del usuario.
    """

    __tablename__ = "data_sync_state"
    __table_args__ = (
        UniqueConstraint("resource_type", "resource_key", name="uq_data_sync_state_resource"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)

    # quote | price_history | fundamentals | fx | asset_metadata
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # Símbolo, o par de divisas para el tipo `fx`.
    resource_key: Mapped[str] = mapped_column(String(120), nullable=False)

    last_attempt_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)
    last_success_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)

    # Backoff exponencial con jitter: no reintentar antes de esta hora.
    next_eligible_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)

    ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=900)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))

    # Detecta respuestas idénticas y evita escrituras inútiles.
    payload_hash: Mapped[str | None] = mapped_column(String(64))

    def __repr__(self) -> str:
        return f"<DataSyncState {self.resource_type}:{self.resource_key}>"


class SyncRun(Base):
    """Una ejecución del pipeline de ingesta.

    Complementa a `DataSyncState`, que responde "¿está fresco ESTE recurso?".
    Esta responde "¿cuándo corrió el pipeline, qué escribió y qué falló?", que
    es lo que necesitan la UI para mostrar la última sincronización y el
    planificador para decidir si toca recuperar una ejecución perdida.

    Es también el hogar de la PROCEDENCIA que se sacó de `price_history`:
    guardar `source` y `fetched_at` en cada una de las cientos de miles de
    barras repetía ~64 bytes por fila para un dato que es de la ingesta.
    """

    __tablename__ = "sync_runs"
    __table_args__ = (
        # La UI pide "el último run"; este índice lo resuelve sin barrer.
        Index("ix_sync_runs_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(IdType, primary_key=True)

    trigger: Mapped[SyncTrigger] = mapped_column(
        sa_enum(SyncTrigger, "sync_trigger"), nullable=False
    )
    status: Mapped[SyncStatus] = mapped_column(
        sa_enum(SyncStatus, "sync_status"), nullable=False, default=SyncStatus.RUNNING
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="yfinance")

    started_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, nullable=False)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UtcDateTime)

    symbols_requested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    symbols_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quotes_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bars_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fundamentals_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fx_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Texto libre y no JSON: son mensajes para leer, no para consultar.
    warnings: Mapped[str | None] = mapped_column(Text)

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def __repr__(self) -> str:
        return f"<SyncRun id={self.id} {self.trigger} {self.status} at={self.started_at}>"
