"""Motor de sugerencia: la siguiente mejor compra para ESTA cartera.

El ranking de oportunidades ordena activos por sus propios méritos. Este motor
responde otra pregunta: de esos, ¿cuál encaja mejor en lo que YA tienes?

    SugerenciaScore = OpportunityScore x FactorCorrelación x FactorConcentración

Los dos factores son multiplicadores acotados, no sumandos, y eso es
deliberado: un activo excelente en un sector donde ya tienes el 60% debe caer
de forma proporcional, no perder unos puntos fijos que su score compensa.

    FactorCorrelación   = 1 + 0.5·(-rho)     ->  [0.5, 1.5]
        rho = +1  ->  0.5   duplica lo que ya tienes: media puntuación
        rho =  0  ->  1.0   independiente: neutro
        rho = -1  ->  1.5   cubre lo que tienes: prima

    FactorConcentración = 1                                   si w <= umbral
                          max(0.25, 1 - 0.75·(w-u)/(1-u))     si w > umbral
        Cae de 1,0 en el umbral a 0,25 con toda la cartera en ese cubo.

GUARDA DE CALIDAD
=================
No se sugiere nada calificado D o E, ni sin calificar. Sin esa guarda, este
motor reintroduciría el problema que la calificación absoluta vino a resolver:
presentar como "la mejor compra" al menos malo de un conjunto malo, ahora con
la autoridad añadida de un botón que dice «Sugerencia óptima».
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.money import ZERO
from app.models import Asset, Portfolio, PriceHistory
from app.schemas.opportunity import OpportunityRead
from app.services import correlation as corr
from app.services import exposure as exposure_service
from app.services import portfolio as portfolio_service

logger = logging.getLogger(__name__)

# Peso hipotético de la compra sugerida. 5% es una posición inicial razonable y
# suficiente para que el efecto sobre la volatilidad sea medible sin dominar la
# cartera. Se declara en la respuesta: el impacto depende de este supuesto.
ASSUMED_WEIGHT = 0.05

def concentration_threshold() -> float:
    """Por encima de esto, un cubo se considera concentrado.

    UN SOLO UMBRAL EN TODO EL SISTEMA. Antes había dos: aquí 25% y en el motor
    de oportunidades 30%, y la MISMA concentración se penalizaba dos veces con
    dos criterios distintos, una en el score y otra en el multiplicador. Al
    salir la diversificación del score (ver `opportunities`), esta es la única
    penalización que queda, y toma el valor configurable.

    Se lee de `settings` en cada llamada, no como constante de módulo, para
    que un cambio por entorno o en un test se vea sin reimportar.
    """
    return settings.opportunity_sector_threshold

# Calificaciones que se pueden sugerir.
ACCEPTABLE_GRADES = {"A", "B", "C"}

CORRELATION_LOOKBACK_DAYS = 400


@dataclass
class SuggestionCandidate:
    opportunity: OpportunityRead
    asset: Asset
    correlation: float | None
    correlation_factor: float
    concentration_factor: float
    bucket_weight: float
    volatility: corr.VolatilityImpact | None
    final_score: float
    reasons: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


def _load_series(
    db: Session, asset_ids: list[int], days: int
) -> dict[int, list[tuple[dt.date, float]]]:
    """Series (fecha, precio ajustado) de varios activos, en una consulta."""
    if not asset_ids:
        return {}

    cutoff = dt.date.today() - dt.timedelta(days=days)
    rows = db.execute(
        select(PriceHistory.asset_id, PriceHistory.date, PriceHistory.adj_close, PriceHistory.close)
        .where(PriceHistory.asset_id.in_(asset_ids), PriceHistory.date >= cutoff)
        .order_by(PriceHistory.asset_id, PriceHistory.date)
    ).all()

    series: dict[int, list[tuple[dt.date, float]]] = {}
    for asset_id, day, adj_close, close in rows:
        price = adj_close if adj_close is not None else close
        if price is not None and price > 0:
            series.setdefault(asset_id, []).append((day, price))
    return series


def correlation_factor(rho: float | None) -> float:
    """[0.5, 1.5]. Un rho desconocido es NEUTRO, nunca una prima.

    Devolver 1.0 ante `None` evita el fallo más traicionero de este motor:
    premiar por descorrelación a un activo cuya correlación nadie ha podido
    medir por falta de histórico solapado.
    """
    if rho is None:
        return 1.0
    return 1.0 + 0.5 * (-max(-1.0, min(1.0, rho)))


def concentration_factor(weight: float, threshold: float | None = None) -> float:
    """1.0 hasta el umbral; cae hasta 0.25 con toda la cartera en ese cubo."""
    threshold = concentration_threshold() if threshold is None else threshold
    weight = max(0.0, min(1.0, weight))
    if weight <= threshold:
        return 1.0
    excess = (weight - threshold) / (1.0 - threshold)
    return max(0.25, 1.0 - 0.75 * excess)


def _build_reasons(candidate: SuggestionCandidate, base_currency: str) -> None:
    """Justificación en texto plano, con las cifras que la sustentan."""
    asset = candidate.asset
    bucket = exposure_service.exposure_bucket(asset)
    weight_pct = candidate.bucket_weight * 100

    if candidate.bucket_weight == 0:
        candidate.reasons.append(
            f"«{bucket}» tiene 0% de peso en tu cartera: es exposición nueva."
        )
    elif candidate.bucket_weight <= concentration_threshold():
        candidate.reasons.append(
            f"«{bucket}» pesa {weight_pct:.1f}%, por debajo del umbral de "
            f"concentración ({concentration_threshold() * 100:.0f}%)."
        )
    else:
        candidate.caveats.append(
            f"«{bucket}» ya pesa {weight_pct:.1f}%: la sugerencia se penalizó "
            f"por concentración."
        )

    if candidate.correlation is not None:
        rho = candidate.correlation
        if rho < 0:
            candidate.reasons.append(
                f"Su correlación con tu cartera es negativa ({rho:+.2f}): tiende "
                f"a moverse en sentido contrario."
            )
        elif rho < 0.4:
            candidate.reasons.append(
                f"Su correlación con tu cartera es baja ({rho:+.2f}): aporta un "
                f"comportamiento distinto al que ya tienes."
            )
        else:
            candidate.caveats.append(
                f"Su correlación con tu cartera es alta ({rho:+.2f}): se parece "
                f"a lo que ya tienes."
            )
    else:
        candidate.caveats.append(
            "No hay histórico solapado suficiente para medir su correlación: se "
            "trató como neutra, no como descorrelacionada."
        )

    impact = candidate.volatility
    if impact is not None:
        # En puntos porcentuales y con el "antes → después" explícito: "un X%"
        # sobre una cantidad que ya es un porcentaje se lee como puntos y no
        # como cambio relativo, y la diferencia es enorme (6% relativo != 6 pp).
        if impact.delta_pp <= -0.1:
            candidate.reasons.append(
                f"Añadir un {impact.weight:.0%} bajaría la volatilidad anual de tu "
                f"cartera de {impact.current_pct:.1f}% a {impact.simulated_pct:.1f}%: "
                f"{abs(impact.delta_pp):.1f} puntos porcentuales menos."
            )
        elif impact.delta_pp >= 0.1:
            candidate.caveats.append(
                f"Añadir un {impact.weight:.0%} subiría la volatilidad anual de tu "
                f"cartera de {impact.current_pct:.1f}% a {impact.simulated_pct:.1f}%: "
                f"{impact.delta_pp:.1f} puntos porcentuales más."
            )

    grade = candidate.opportunity.assessment
    candidate.reasons.append(
        f"Calificación absoluta «{grade.label}» ({grade.points}/{grade.max_points}), "
        f"independiente de contra quién se compare."
    )


def suggest(
    db: Session,
    portfolio: Portfolio,
    opportunities: list[OpportunityRead],
    *,
    assets_by_symbol: dict[str, Asset],
    weight: float = ASSUMED_WEIGHT,
) -> tuple[SuggestionCandidate | None, list[SuggestionCandidate], list[str]]:
    """Devuelve (ganador, todos los evaluados ordenados, avisos)."""
    warnings: list[str] = []

    summary = portfolio_service.get_summary(db, portfolio)
    bucket_weights = portfolio_service.sector_weights(summary.positions)

    # Lo que YA se tiene, para no sugerirlo. Se toma del ledger -todas las
    # posiciones abiertas- y NO de las valoradas: sin tipo de cambio nada se
    # valora, y entonces el motor recomendaría comprar algo que ya está en
    # cartera. La valoración sirve para PESAR, no para saber qué se posee.
    held_symbols = {p.symbol for p in summary.positions}

    # --- serie de retornos de la cartera actual ---
    valued = {p.symbol: p for p in summary.positions if p.market_value is not None}
    held_assets = {
        assets_by_symbol[s].id: p for s, p in valued.items() if s in assets_by_symbol
    }

    # La serie de la cartera se indexa POR FECHA, no por posición: cada
    # candidato se correlacionará después sobre las fechas que comparta con
    # ella, que son distintas para cada uno (una cripto cotiza en fin de
    # semana; un ADR europeo tiene otros festivos).
    base_by_date: dict[dt.date, float] = {}
    if held_assets:
        total_value = sum((p.market_value for p in held_assets.values()), ZERO)
        if total_value > ZERO:
            weights = {
                asset_id: float(position.market_value / total_value)
                for asset_id, position in held_assets.items()
            }
            series = _load_series(db, list(held_assets), CORRELATION_LOOKBACK_DAYS)
            dates, returns = corr.align_returns(series)
            combined = corr.portfolio_returns(weights, returns)
            if combined:
                base_by_date = dict(zip(dates[-len(combined):], combined, strict=True))

    if not base_by_date:
        warnings.append(
            "Tu cartera no tiene histórico suficiente para medir correlaciones: "
            "la sugerencia se basa solo en el score y en la concentración por sector."
        )

    if summary.positions and not bucket_weights:
        # Hay posiciones pero ninguna valorada: sin pesos no se puede penalizar
        # la concentración, y callarlo daría a entender que la cartera está
        # diversificada cuando en realidad no se ha podido medir.
        warnings.append(
            "Ninguna posición tiene valor de mercado (falta precio o tipo de "
            "cambio): no se pudo evaluar la concentración por sector. Sincroniza "
            "los datos de mercado para una sugerencia completa."
        )

    # --- evaluar candidatos ---
    candidate_ids = [
        assets_by_symbol[o.symbol].id
        for o in opportunities
        if o.symbol in assets_by_symbol
    ]
    candidate_series = _load_series(db, candidate_ids, CORRELATION_LOOKBACK_DAYS)

    evaluated: list[SuggestionCandidate] = []
    skipped_by_grade = 0

    for opportunity in opportunities:
        asset = assets_by_symbol.get(opportunity.symbol)
        if asset is None:
            continue

        # Guarda de calidad: no se sugiere lo que la calificación absoluta
        # señala como malo, por muy arriba que esté en el ranking relativo.
        if opportunity.assessment.grade not in ACCEPTABLE_GRADES:
            skipped_by_grade += 1
            continue

        # Ya en cartera: no es una sugerencia de "siguiente compra".
        if opportunity.symbol in held_symbols:
            continue

        bucket = exposure_service.exposure_bucket(asset)
        bucket_weight = float(bucket_weights.get(bucket, Decimal("0")))

        rho = None
        impact = None
        own = candidate_series.get(asset.id)
        if base_by_date and own:
            # Solo las fechas que ambos comparten. Sin esta intersección se
            # emparejarían días distintos y la correlación sería inventada.
            base_paired, own_paired = corr.paired(base_by_date, corr.returns_by_date(own))
            if base_paired:
                rho = corr.pearson(base_paired, own_paired)
                impact = corr.volatility_impact(base_paired, own_paired, weight)

        corr_f = correlation_factor(rho)
        conc_f = concentration_factor(bucket_weight)

        candidate = SuggestionCandidate(
            opportunity=opportunity,
            asset=asset,
            correlation=rho,
            correlation_factor=corr_f,
            concentration_factor=conc_f,
            bucket_weight=bucket_weight,
            volatility=impact,
            final_score=opportunity.score * corr_f * conc_f,
        )
        _build_reasons(candidate, portfolio.base_currency)
        evaluated.append(candidate)

    if skipped_by_grade:
        warnings.append(
            f"{skipped_by_grade} candidato(s) con score alto quedaron descartados "
            f"por calificación absoluta insuficiente."
        )

    evaluated.sort(key=lambda c: c.final_score, reverse=True)
    return (evaluated[0] if evaluated else None), evaluated, warnings
