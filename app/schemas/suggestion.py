from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.opportunity import OpportunityRead


class VolatilityImpactRead(BaseModel):
    """Efecto medido de añadir el activo, bajo el peso supuesto."""

    model_config = ConfigDict(extra="forbid")

    weight_pct: float = Field(description="Peso hipotético de la compra, en %")
    current_volatility_pct: float = Field(description="Volatilidad anual actual, en %")
    simulated_volatility_pct: float
    delta_pct: float = Field(description="Variación relativa. Negativa = reduce riesgo")
    overlap_days: int = Field(description="Días con datos en ambas series")


class SuggestionFactors(BaseModel):
    """Los multiplicadores que llevan del score al puesto sugerido."""

    model_config = ConfigDict(extra="forbid")

    opportunity_score: float
    correlation: float | None = Field(description="Pearson vs tu cartera. None si no medible")
    correlation_factor: float = Field(description="[0.5, 1.5]")
    bucket_weight_pct: float = Field(description="Peso actual de su cubo en tu cartera")
    concentration_factor: float = Field(description="[0.25, 1.0]")
    final_score: float = Field(description="score x correlación x concentración")


class SuggestionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str | None
    exposure_bucket: str
    current_price: float | None
    currency: str

    rank_in_opportunities: int
    grade: str
    grade_label: str

    factors: SuggestionFactors
    volatility_impact: VolatilityImpactRead | None

    # Justificación en texto plano, cada frase respaldada por una cifra.
    reasons: list[str]
    caveats: list[str]
    headline: str = Field(description="Resumen de una frase, listo para mostrar")

    # Lo necesario para encadenar con el simulador sin volver a buscar.
    opportunity: OpportunityRead


class SuggestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    base_currency: str
    as_of: dt.datetime

    suggestion: SuggestionRead | None
    runners_up: list[SuggestionRead] = Field(default_factory=list)

    candidates_evaluated: int
    assumed_weight_pct: float
    concentration_threshold_pct: float

    warnings: list[str] = Field(default_factory=list)
    no_suggestion_reason: str | None = Field(
        default=None,
        description="Por qué no hay sugerencia. Preferible a devolver una mala",
    )

    disclaimer: str = Field(
        default=(
            "Esta sugerencia optimiza el encaje de un activo en tu cartera según "
            "métricas históricas de valoración, tendencia, riesgo y correlación. "
            "NO es asesoramiento financiero. La correlación pasada no garantiza "
            "la futura, y el impacto en volatilidad supone un peso concreto de "
            "compra que se declara en la respuesta."
        )
    )
