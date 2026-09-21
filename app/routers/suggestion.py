"""Sugerencia óptima: la siguiente mejor compra para una cartera concreta."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, BackgroundTasks, Query

from app.core.exceptions import InsufficientUniverse
from app.repositories import portfolio as portfolio_repo
from app.routers.dependencies import DbSession, ProviderDep, SessionFactory
from app.schemas.suggestion import (
    SuggestionFactors,
    SuggestionRead,
    SuggestionResponse,
    VolatilityImpactRead,
)
from app.services import opportunities as opportunity_service
from app.services import refresh_jobs
from app.services import suggestion as suggestion_service
from app.services import universe as universe_service

router = APIRouter(prefix="/api/portfolios", tags=["suggestion"])


def _to_read(candidate: suggestion_service.SuggestionCandidate) -> SuggestionRead:
    opportunity = candidate.opportunity
    impact = candidate.volatility

    # La frase de titular junta las dos razones más fuertes. Se construye aquí y
    # no en el frontend para que la API sea usable sin interfaz.
    headline = f"Te sugerimos {candidate.asset.symbol}"
    if candidate.bucket_weight == 0:
        headline += f": «{opportunity.exposure_bucket}» tiene 0% de peso en tu cartera"
    else:
        headline += (
            f": ocupa el puesto #{opportunity.rank} y «{opportunity.exposure_bucket}» "
            f"pesa {candidate.bucket_weight * 100:.1f}%"
        )
    if impact is not None and impact.delta_pp <= -0.1:
        headline += (
            f" y bajaría la volatilidad de tu cartera de {impact.current_pct:.1f}% "
            f"a {impact.simulated_pct:.1f}%"
        )
    headline += "."

    rho = candidate.correlation
    return SuggestionRead(
        symbol=candidate.asset.symbol,
        name=candidate.asset.name,
        exposure_bucket=opportunity.exposure_bucket,
        current_price=opportunity.current_price,
        currency=candidate.asset.currency,
        rank_in_opportunities=opportunity.rank,
        grade=opportunity.assessment.grade,
        grade_label=opportunity.assessment.label,
        factors=SuggestionFactors(
            opportunity_score=round(opportunity.score, 2),
            correlation=round(rho, 4) if rho is not None else None,
            correlation_factor=round(candidate.correlation_factor, 3),
            bucket_weight_pct=round(candidate.bucket_weight * 100, 2),
            concentration_factor=round(candidate.concentration_factor, 3),
            final_score=round(candidate.final_score, 2),
        ),
        volatility_impact=(
            VolatilityImpactRead(
                weight_pct=round(impact.weight * 100, 2),
                current_volatility_pct=impact.current_pct,
                simulated_volatility_pct=impact.simulated_pct,
                delta_pp=impact.delta_pp,
                overlap_days=impact.overlap_days,
            )
            if impact
            else None
        ),
        reasons=candidate.reasons,
        caveats=candidate.caveats,
        headline=headline,
        opportunity=opportunity,
    )


@router.get("/{portfolio_id}/suggested-stock", response_model=SuggestionResponse)
def suggested_stock(
    db: DbSession,
    provider: ProviderDep,
    session_factory: SessionFactory,
    background: BackgroundTasks,
    portfolio_id: int,
    pool: int = Query(25, ge=5, le=50, description="Cuántas oportunidades se consideran"),
    weight: float = Query(
        0.05, gt=0, le=0.5, description="Peso hipotético de la compra, para medir el impacto"
    ),
    refresh: bool = Query(
        True,
        description=(
            "Lanzar un refresco de precios POR DETRÁS. Nunca retrasa esta "
            "respuesta"
        ),
    ),
):
    """La siguiente mejor compra, optimizando encaje en la cartera actual.

    No devuelve simplemente el primero del ranking: reordena esos candidatos
    por cuánto MEJORAN esta cartera, penalizando la concentración sectorial y
    premiando la baja correlación con lo que ya se tiene.

    Puede no devolver nada. Si ningún candidato supera la guarda de calidad,
    `suggestion` viene en null con el motivo en `no_suggestion_reason`: es
    preferible a recomendar algo malo bajo un botón que promete lo óptimo.
    """
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)

    assets = universe_service.get_ingestion_universe(db)
    if len(assets) < opportunity_service.settings.opportunity_min_universe:
        raise InsufficientUniverse(
            f"Solo hay {len(assets)} activos con datos. Sincroniza o añade "
            f"símbolos antes de pedir una sugerencia."
        )

    # Mismo criterio que en oportunidades: el job completo no cabe en una
    # petición. Aquí el universo ya está ingestado -si no, arriba habría
    # saltado `InsufficientUniverse`-, así que no hay nada que cargar en línea
    # y el refresco de precios se encola para después de responder.
    warnings: list[str] = []
    if refresh and assets:
        background.add_task(
            refresh_jobs.refresh_quotes_for,
            session_factory,
            provider,
            [a.symbol for a in assets],
            portfolio.base_currency,
            scope=f"opportunities:{portfolio.id}",
        )

    ranking = opportunity_service.compute_opportunities(
        db, portfolio, assets=assets, limit=pool
    )
    warnings.extend(ranking.warnings)

    assets_by_symbol = {a.symbol: a for a in assets}
    winner, evaluated, notes = suggestion_service.suggest(
        db, portfolio, ranking.opportunities,
        assets_by_symbol=assets_by_symbol, weight=weight,
    )
    warnings.extend(notes)

    reason = None
    if winner is None:
        reason = (
            "Ningún candidato pasa el filtro: o están todos calificados como "
            "malos, o ya los tienes en cartera. Amplía el universo con más "
            "símbolos o revisa la vista de Oportunidades."
        )

    return SuggestionResponse(
        portfolio_id=portfolio.id,
        base_currency=portfolio.base_currency,
        as_of=dt.datetime.now(dt.UTC),
        suggestion=_to_read(winner) if winner else None,
        runners_up=[_to_read(c) for c in evaluated[1:4]],
        candidates_evaluated=len(evaluated),
        assumed_weight_pct=round(weight * 100, 2),
        concentration_threshold_pct=suggestion_service.concentration_threshold() * 100,
        warnings=warnings,
        no_suggestion_reason=reason,
    )
