from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field


class FactorDetail(BaseModel):
    """Un factor del score, con su rastro completo hasta el dato observado."""

    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=100, description="Factor normalizado 0-100")
    weight: float = Field(description="Peso en la fórmula. Negativo si se resta")
    contribution: float = Field(description="score * weight: puntos aportados al total")
    available: bool = Field(description="False si se imputó el valor neutro (50)")
    inputs: dict[str, float | None] = Field(
        default_factory=dict,
        description="Métricas OBSERVADAS que alimentan el factor, sin normalizar",
    )


class SignalDetail(BaseModel):
    """Una señal absoluta de la calificación, con su rastro hasta el dato."""

    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    points: int | None = Field(description="-2 a +2. None si no hay datos")
    detail: str
    inputs: dict[str, float | None] = Field(default_factory=dict)


class AbsoluteAssessment(BaseModel):
    """Calificación que NO depende del universo evaluado.

    Existe porque el score es puramente ordinal: un universo de candidatos
    malos produce igualmente un primer puesto con score alto. Esta calificación
    compara contra el mercado y contra umbrales fijos, así que puede decir
    "el mejor de estos sigue siendo malo".
    """

    model_config = ConfigDict(extra="forbid")

    grade: str = Field(description="A | B | C | D | E | SIN_CALIFICAR")
    label: str = Field(description="Muy buena | Buena | Normal | Mala | Muy mala")
    points: int
    max_points: int
    signals: list[SignalDetail]
    notes: list[str] = Field(default_factory=list)


class OpportunityRead(BaseModel):
    """Una fila del ranking, con el desglose que la hace interpretable."""

    model_config = ConfigDict(extra="forbid")

    rank: int
    symbol: str
    name: str | None
    sector: str | None
    currency: str
    market_region: str = Field(
        description="US | COL | LATAM | EU | ASIA | GLOBAL, por domicilio de la empresa"
    )

    score: float = Field(ge=0, le=100)
    baseline: float = Field(
        description="Desplazamiento afín (+20) que mapea el rango natural [-20,80] a [0,100]"
    )

    value: FactorDetail
    momentum: FactorDetail
    diversification: FactorDetail
    risk: FactorDetail

    current_price: float | None
    exposure_bucket: str = Field(
        description="Cubo de exposición usado para diversificar: sector GICS o clase de activo"
    )
    exposure_is_assumed: bool = Field(
        default=False, description="El cubo salió de una heurística, no de un dato explícito"
    )
    sector_weight_pct: float | None = Field(
        description="Peso actual de ese cubo en tu cartera, en %"
    )

    assessment: AbsoluteAssessment

    data_completeness: float = Field(
        ge=0, le=1, description="Fracción de factores informativos con dato real"
    )
    confidence: str = Field(description="high | medium | low, derivado de data_completeness")
    notes: list[str] = Field(default_factory=list)


class OpportunityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    base_currency: str
    as_of: dt.datetime

    universe_size: int = Field(description="Candidatos evaluados tras filtrar por datos")
    universe_requested: int = Field(description="Candidatos considerados antes de filtrar")
    matched_size: int = Field(
        description="Candidatos que pasan los filtros de calidad y región. "
        "Igual a `universe_size` cuando no hay filtros activos"
    )
    opportunities: list[OpportunityRead]

    formula: str
    weights: dict[str, float]
    sector_threshold_pct: float

    benchmark_symbol: str | None = Field(
        default=None, description="Referencia de mercado usada para la valoración absoluta"
    )
    benchmark_pe: float | None = None
    grade_counts: dict[str, int] = Field(
        default_factory=dict, description="Cuántos candidatos hay en cada calificación"
    )
    region_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Cuántos candidatos hay en cada región. Se cuenta sobre el "
        "universo COMPLETO, no sobre el filtrado: es lo que permite volver a "
        "activar un filtro que ahora mismo no muestra nada",
    )
    universe_quality_warning: str | None = Field(
        default=None,
        description="Aviso cuando NINGÚN candidato alcanza una calificación buena",
    )

    warnings: list[str] = Field(default_factory=list)
    excluded: dict[str, str] = Field(
        default_factory=dict, description="símbolo -> motivo de exclusión"
    )

    disclaimer: str = Field(
        default=(
            "Este score es un RANKING RELATIVO dentro del universo de candidatos "
            "evaluado, no una valoración absoluta ni una recomendación de "
            "inversión. Un universo compuesto solo por malas acciones produce "
            "igualmente un primer puesto con score alto. Los pesos son un juicio "
            "de diseño, no el resultado de un backtest."
        )
    )
