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
    label: str = Field(
        description="Muy favorables | Favorables | Mixtas | Desfavorables | "
        "Muy desfavorables. Habla de las SEÑALES medidas, no de la empresa."
    )
    points: int
    max_points: int
    signals: list[SignalDetail]
    notes: list[str] = Field(default_factory=list)
    available_signals: int = Field(
        default=0, description="Cuántas de las 4 señales tienen datos"
    )
    capped_by_coverage: bool = Field(
        default=False,
        description=(
            "La nota está limitada por falta de señales: la proporción daría "
            "más, pero con menos de 3 señales eso premiaría la falta de datos"
        ),
    )


class DataFreshness(BaseModel):
    """De cuándo son los datos que alimentan el ranking.

    Existe porque un ranking se lee como si fuera de HOY. Con los precios de
    hace una semana y los fundamentales de hace dos, el orden puede ser otro, y
    nada en la pantalla lo delataba.
    """

    model_config = ConfigDict(extra="forbid")

    prices_newest: dt.datetime | None = None
    prices_oldest: dt.datetime | None = None
    fundamentals_newest: dt.date | None = None
    fundamentals_oldest: dt.date | None = None
    fundamentals_fresh_pct: float | None = Field(
        default=None,
        description=(
            "Qué porcentaje del universo tiene fundamentales recientes. "
            "`fundamentals_oldest` por sí solo no distingue UN activo viejo "
            "de la mitad del universo viejo, y son cosas muy distintas"
        ),
    )
    fundamentals_stale_count: int = Field(
        default=0, description="Cuántos candidatos puntúan con datos viejos"
    )


class OpportunityRead(BaseModel):
    """Una fila del ranking, con el desglose que la hace interpretable."""

    model_config = ConfigDict(extra="forbid")

    rank: int = Field(
        description="Puesto DENTRO DE SU CLASE en el ranking completo por score, "
        "con huecos cuando la vista está filtrada"
    )
    rank_in_grade: int = Field(
        default=0, description="Puesto entre los de su misma clase y calificación"
    )
    symbol: str
    name: str | None
    sector: str | None
    currency: str
    market_region: str = Field(
        description="US | COL | LATAM | EU | ASIA | GLOBAL, por domicilio de la empresa"
    )

    asset_class: str = Field(
        default="accion",
        description=(
            "accion | fondo_acciones | renta_fija | materias_primas | cripto | "
            "derivado | desconocido. El score es un rango percentil DENTRO de "
            "esta clase: no es comparable con el de otra."
        ),
    )
    asset_class_label: str = Field(default="Acciones")
    expense_ratio_pct: float | None = Field(
        default=None,
        description=(
            "Coste anual del fondo, en porcentaje. Es de lo poco que predice "
            "rendimiento futuro de forma fiable: se resta todos los años, "
            "haya subido o bajado el mercado"
        ),
    )
    asset_class_is_assumed: bool = Field(
        default=False,
        description="La clase salió de una suposición y no de un dato del proveedor",
    )
    class_size: int = Field(
        default=0,
        description=(
            "Cuántos candidatos hay en esta clase. Es la referencia contra la "
            "que se calculó el score, y con pocos miembros discrimina poco."
        ),
    )

    score: float = Field(ge=0, le=100)
    baseline: float = Field(
        description="Desplazamiento afín que lleva el mínimo a 0; la escala a [0,100] "
        "se completa dividiendo por la suma de pesos"
    )

    value: FactorDetail
    momentum: FactorDetail
    diversification: FactorDetail
    risk: FactorDetail

    current_price: float | None
    price_as_of: dt.datetime | None = Field(
        default=None, description="Cuándo se cotizó ese precio"
    )
    fundamentals_as_of: dt.date | None = Field(
        default=None, description="Fecha de los fundamentales usados"
    )
    fundamentals_age_days: int | None = Field(
        default=None,
        description=(
            "Días desde esos fundamentales. Por encima del umbral la "
            "confianza de la fila baja: un dato viejo puntúa igual de bien "
            "que uno fresco y nada en la pantalla lo delataba"
        ),
    )
    value_basis: str = Field(
        default="class",
        description="Contra qué se ordenaron los múltiplos: 'sector' (sus pares del "
        "mismo sector) o 'class' (su clase de activo, si el sector tiene pocos pares "
        "o no aplica)",
    )
    value_reference: str | None = Field(
        default=None, description="Sector o clase usado como referencia de valoración"
    )
    sector_pe: float | None = Field(
        default=None, description="Mediana del P/E trailing de su sector (referencia)"
    )
    sector_peer_count: int | None = Field(
        default=None, description="Empresas con P/E positivo en que se basa esa mediana"
    )
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

    freshness: DataFreshness = Field(default_factory=DataFreshness)
    benchmark_symbol: str | None = Field(
        default=None, description="Referencia de mercado usada para la valoración absoluta"
    )
    benchmark_pe: float | None = None
    class_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Cuántos candidatos hay en cada clase de activo, sobre el "
        "universo completo y no sobre el resultado filtrado",
    )
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

    refreshing: bool = Field(
        default=False,
        description=(
            "Hay un refresco de precios en curso por detrás. La respuesta ya "
            "es válida -está puntuada con los últimos datos guardados-; "
            "cuando esto sea true conviene volver a pedirla en unos segundos "
            "para ver los precios recién traídos"
        ),
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
