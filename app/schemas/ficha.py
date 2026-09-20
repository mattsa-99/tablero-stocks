"""Esquemas de la ficha de compra: banderas, veredicto y secciones."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.opportunity import OpportunityRead

FlagLevel = Literal["red", "yellow", "green", "info"]


class Flag(BaseModel):
    """Una observación con nombre estable, nivel y la cifra que la sostiene.

    `code` es estable a propósito: el frontend, los tests y el diario se apoyan
    en él, y un texto reescrito no debe romper nada. `evidence` lleva el dato
    exacto para que la bandera se pueda comprobar y no solo creer.
    """

    model_config = ConfigDict(extra="forbid")

    code: str
    level: FlagLevel
    title: str
    detail: str
    evidence: dict[str, float | int | str | None] = Field(default_factory=dict)


class Verdict(BaseModel):
    """Resumen de las banderas. NUNCA dice «compra»: dice si el filtro encontró
    problemas, que es una afirmación mucho más modesta y mucho más verdadera."""

    model_config = ConfigDict(extra="forbid")

    level: Literal["red", "yellow", "green"]
    label: str
    red: int
    yellow: int
    green: int
    info: int
    disclaimer: str = (
        "Esto NO es una recomendación de compra. Sin banderas rojas solo "
        "significa que este filtro no encontró problemas: no mide el negocio, "
        "la gerencia, la competencia ni el futuro."
    )


# ---------------------------------------------------------------------------
# Ficha de compra: todo lo que hay que mirar de UNA empresa, en un solo lugar
# ---------------------------------------------------------------------------


class CalendarRead(BaseModel):
    """Fechas futuras. `None` = no consta una fecha futura (no «no hay»)."""

    model_config = ConfigDict(extra="forbid")

    next_earnings: dt.date | None = None
    earnings_is_estimate: bool = False
    days_to_earnings: int | None = None
    ex_dividend: dt.date | None = None
    days_to_ex_dividend: int | None = None


class AnalystRead(BaseModel):
    """Consenso de analistas: OPINIÓN de terceros, no un dato."""

    model_config = ConfigDict(extra="forbid")

    target_mean: float | None = None
    analysts: int | None = None
    recommendation: str | None = None
    upside_pct: float | None = None


class HealthRead(BaseModel):
    """Salud financiera. Cocientes (fracciones o múltiplos), nunca cifras
    absolutas mezcladas entre monedas."""

    model_config = ConfigDict(extra="forbid")

    has_data: bool
    applies: bool
    not_applicable_reason: str | None = None
    reporting_currency: str | None = None
    quote_currency: str | None = None
    currency_mismatch: bool = False
    net_debt_to_ebitda: float | None = None
    fcf_margin: float | None = None
    fcf_yield: float | None = None
    operating_margin: float | None = None
    current_ratio: float | None = None
    payout_ratio: float | None = None
    calendar: CalendarRead = Field(default_factory=CalendarRead)
    analyst: AnalystRead = Field(default_factory=AnalystRead)
    caveats: list[str] = Field(default_factory=list)
    unavailable: dict[str, str] = Field(default_factory=dict)


class PeerRead(BaseModel):
    """Un competidor del mismo sector dentro del universo evaluado."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str | None
    rank: int
    score: float
    grade: str
    grade_label: str
    trailing_pe: float | None = None


class HoldingRead(BaseModel):
    """Lo que YA tienes de este activo. Importes en divisa base."""

    model_config = ConfigDict(extra="forbid")

    quantity: str
    weight_pct: float | None = None
    market_value: str | None = None
    unrealized_return_pct: float | None = None


class PortfolioContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    base_currency: str
    position_count: int
    holding: HoldingRead | None = None
    bucket: str | None = None
    bucket_weight_pct: float | None = None
    total_value: str | None = None


class Freshness(BaseModel):
    """De cuándo es cada dato. Es lo que permite fiarse (o no) de la ficha."""

    model_config = ConfigDict(extra="forbid")

    price_time: dt.datetime | None = None
    price_is_stale: bool | None = None
    last_bar_date: dt.date | None = None
    fundamentals_as_of: dt.date | None = None
    fundamentals_age_days: int | None = None
    notes: list[str] = Field(default_factory=list)


class SizingRead(BaseModel):
    """Cuánto poner en esta empresa: presupuesto de riesgo / caída de estrés."""

    model_config = ConfigDict(extra="forbid")

    risk_budget_pct: float
    max_position_pct: float
    stress_loss_pct: float
    stress_source: Literal["observed", "floor"]
    observed_drawdown_pct: float | None = None
    target_pct: float
    binding: Literal["risk", "cap"]
    current_pct: float
    add_pct: float
    capital: Decimal | None = None
    base_currency: str
    target_amount: Decimal | None = None
    add_amount: Decimal | None = None
    loss_if_repeats_amount: Decimal | None = None
    loss_if_repeats_pct_of_capital: float
    notes: list[str] = Field(default_factory=list)


class FichaResponse(BaseModel):
    """La ficha de compra. Fuente única: la usan la interfaz, el script que
    genera el informe en markdown y el diario, para que no se contradigan."""

    model_config = ConfigDict(extra="forbid")

    as_of: dt.datetime
    symbol: str
    name: str | None
    asset_type: str
    sector: str | None
    industry: str | None
    currency: str
    market_region: str | None = None

    # None si el activo está fuera del ranking (sin datos suficientes): en ese
    # caso `excluded_reason` lo explica y solo se ofrecen datos y banderas.
    opportunity: OpportunityRead | None = None
    excluded_reason: str | None = None
    universe_size: int | None = None

    verdict: Verdict
    flags: list[Flag]
    health: HealthRead
    peers: list[PeerRead] = Field(default_factory=list)
    sector_rank: int | None = None
    sector_size: int | None = None
    portfolio: PortfolioContext
    sizing: SizingRead | None = None
    freshness: Freshness
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = (
        "Ficha informativa, NO es una recomendación de compra. Los datos vienen de "
        "Yahoo Finance con retraso y errores posibles: comprueba las cifras "
        "clave en la web de la empresa o en la bolsa antes de decidir."
    )
