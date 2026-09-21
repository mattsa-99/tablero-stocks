"""Esquemas de la renta fija directa: TES, CDT y FIC cargados a mano."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import RateKind


class FixedIncomeCreate(BaseModel):
    """Alta de un instrumento. Todo lo que no se puede deducir va aquí."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(
        min_length=1,
        max_length=20,
        description=(
            "Un identificador TUYO: CDT-BANCOLOMBIA-2027, TES-JUL30. No hay "
            "ticker porque no cotiza en ningún mercado"
        ),
        examples=["CDT-BANCOLOMBIA-2027"],
    )
    name: str | None = Field(default=None, max_length=200)
    currency: str = Field(default="COP", min_length=3, max_length=3)
    issuer: str = Field(min_length=1, max_length=120)

    rate_kind: RateKind = Field(default=RateKind.FIXED)
    annual_rate_pct: Decimal = Field(
        description=(
            "Tasa efectiva anual si es FIJA; si está indexada, el SPREAD sobre "
            "el índice en puntos (IBR + 2,50 se envía como 2.50)"
        )
    )
    issued_on: dt.date
    matures_on: dt.date
    has_secondary_market: bool = Field(
        default=False,
        description="¿Puedes vender antes del vencimiento? Un CDT normalmente no",
    )
    withholding_pct: Decimal | None = Field(
        default=None,
        ge=0,
        le=100,
        description=(
            "Retención en la fuente, informativa. Sin ella la comparación con "
            "la inflación usa la tasa BRUTA y sale mejor de lo que recibirás"
        ),
    )
    notes: str | None = Field(default=None, max_length=300)


class AccrualRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor: Decimal
    effective_rate_pct: Decimal
    days_elapsed: int
    days_to_maturity: int
    is_matured: bool
    basis: str = Field(description="Siempre 'costo_mas_devengo': no hay precio de mercado")
    caveat: str


class FixedIncomeRead(BaseModel):
    """Las cinco preguntas, contestadas con datos oficiales y fechados.

    NO lleva score ni calificación A-E, y no es un olvido: un score es un rango
    percentil contra pares, y aquí no hay pares medibles.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: str
    issuer: str
    currency: str
    rate_kind: str
    nominal_rate_pct: float
    net_rate_pct: float | None = None
    real_rate_pct: float | None = Field(
        default=None,
        description="Rentabilidad tras la inflación, por Fisher exacto y no por resta",
    )
    inflation_pct: float | None = None
    inflation_as_of: dt.date | None = None
    market_reference_pct: float | None = None
    market_reference_label: str | None = None
    market_reference_as_of: dt.date | None = None
    beats_inflation: bool | None = None
    beats_market: bool | None = None
    best_bank_rate_pct: float | None = Field(
        default=None,
        description="La mejor tasa pactada a este plazo, por banco concreto",
    )
    best_bank_name: str | None = None
    median_bank_rate_pct: float | None = None
    issuer_market_rate_pct: float | None = Field(
        default=None, description="Lo que pagó TU emisor a este plazo"
    )
    banks_compared: int = 0
    bank_term_labels: list[str] = Field(
        default_factory=list,
        description=(
            "Tramos de plazo usados para comparar. No son igual de finos: "
            "«a 360 dias» es exactamente 360 y «superiores a 360 dias» va de "
            "361 en adelante, así que la comparación es más gruesa"
        ),
    )
    bank_rates_as_of: dt.date | None = None
    days_to_maturity: int
    has_secondary_market: bool
    accrual: AccrualRead | None = None
    questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = Field(
        default=(
            "Valorado a costo más devengo: es lo que vale si lo llevas a "
            "vencimiento, no lo que alguien pagaría hoy. No es asesoramiento "
            "financiero y el tablero no calcula impuestos."
        )
    )
