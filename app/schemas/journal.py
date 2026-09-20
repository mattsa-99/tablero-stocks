"""Esquemas del diario de decisiones y la lista de vigilancia."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import JournalKind

# Una tesis de tres palabras es un ritual vacío: el objetivo del diario es
# obligar a articular POR QUÉ. Diez caracteres es el mínimo para que no se
# pueda rellenar con «buena» o «sube».
MIN_THESIS_CHARS = 10
DEFAULT_REVIEW_DAYS = 90


def _strip(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


class JournalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=20)
    kind: JournalKind
    thesis: str = Field(
        max_length=2000,
        description="Por qué. En tus palabras, mínimo una frase",
    )
    invalidation: str | None = Field(
        default=None,
        max_length=1000,
        description="Qué hecho demostraría que te equivocaste",
    )
    invalidation_price: Decimal | None = Field(
        default=None,
        gt=0,
        description="Precio (en la divisa de cotización) por debajo del cual la tesis cae",
    )
    review_date: dt.date | None = Field(
        default=None,
        description=f"Cuándo vuelves a mirar. Por defecto, en {DEFAULT_REVIEW_DAYS} días",
    )

    @field_validator("symbol")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("thesis")
    @classmethod
    def _thesis(cls, value: str) -> str:
        value = value.strip()
        if len(value) < MIN_THESIS_CHARS:
            raise ValueError(
                f"Escribe la razón con al menos {MIN_THESIS_CHARS} caracteres: "
                "si no puedes explicar por qué, todavía no estás listo para decidir"
            )
        return value

    @field_validator("invalidation")
    @classmethod
    def _invalidation(cls, value: str | None) -> str | None:
        return _strip(value)


class JournalUpdate(BaseModel):
    """Cambio parcial: solo los campos enviados se tocan. Enviar `null` en
    `invalidation` o `invalidation_price` los borra."""

    model_config = ConfigDict(extra="forbid")

    kind: JournalKind | None = None
    thesis: str | None = Field(default=None, max_length=2000)
    invalidation: str | None = Field(default=None, max_length=1000)
    invalidation_price: Decimal | None = Field(default=None, gt=0)
    review_date: dt.date | None = None
    is_active: bool | None = None

    @field_validator("thesis")
    @classmethod
    def _thesis(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) < MIN_THESIS_CHARS:
            raise ValueError(f"La razón necesita al menos {MIN_THESIS_CHARS} caracteres")
        return value


class JournalReview(BaseModel):
    """Marcar como revisada: qué viste hoy y cuándo vuelves a mirar."""

    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=3, max_length=2000)
    next_review_date: dt.date | None = Field(
        default=None,
        description=f"Próxima revisión. Por defecto, en {DEFAULT_REVIEW_DAYS} días",
    )
    archive: bool = Field(
        default=False, description="Cerrar la entrada: ya no se vigila ni alerta"
    )

    @field_validator("note")
    @classmethod
    def _note(cls, value: str) -> str:
        return value.strip()


AlertLevel = Literal["red", "yellow", "info"]


class JournalAlert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    level: AlertLevel
    text: str


class SnapshotFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    level: str


class JournalSnapshot(BaseModel):
    """Lo que decía el Tablero el día que anotaste la decisión."""

    model_config = ConfigDict(extra="forbid")

    price: float | None = None
    score: float | None = None
    rank: int | None = None
    grade: str | None = None
    verdict: str | None = None
    flags: list[SnapshotFlag] = Field(default_factory=list)


class JournalNow(BaseModel):
    """Lo que dice hoy, para comparar con la foto."""

    model_config = ConfigDict(extra="forbid")

    price: float | None = None
    price_stale: bool | None = None
    change_since_entry_pct: float | None = None
    score: float | None = None
    rank: int | None = None
    grade: str | None = None
    grade_label: str | None = None


class JournalEntryRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    portfolio_id: int
    symbol: str
    name: str | None
    currency: str
    kind: JournalKind
    is_active: bool
    thesis: str
    invalidation: str | None
    invalidation_price: Decimal | None
    review_date: dt.date
    days_to_review: int
    reviewed_at: dt.datetime | None
    review_note: str | None
    created_at: dt.datetime
    held: bool = False
    snapshot: JournalSnapshot
    now: JournalNow
    alerts: list[JournalAlert] = Field(default_factory=list)


class JournalCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: int = 0
    overdue: int = 0
    breached: int = 0
    with_alerts: int = 0


class JournalListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: dt.datetime
    entries: list[JournalEntryRead]
    counts: JournalCounts
    disclaimer: str = (
        "El diario guarda TUS razones y las compara con lo que dice el Tablero. "
        "No es una recomendación: una alerta te pide revisar, no vender."
    )
