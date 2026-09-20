from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Query

from app.repositories import portfolio as portfolio_repo
from app.routers.dependencies import DbSession
from app.schemas.performance import PerformanceResponse, ValuePointRead
from app.services import performance as performance_service

router = APIRouter(prefix="/api/portfolios", tags=["performance"])


@router.get("/{portfolio_id}/performance", response_model=PerformanceResponse)
def get_performance(
    db: DbSession,
    portfolio_id: int,
    days: int = Query(
        365,
        ge=7,
        le=3650,
        description=(
            "Ventana en días naturales. La curva nunca empieza antes de la "
            "primera transacción, así que pedir más de lo que hay no alarga "
            "nada"
        ),
    ),
):
    """Valor de la cartera día a día, con la línea del índice y el XIRR.

    NO sale a la red: se construye con el ledger y las series ya guardadas. Un
    hueco de datos se declara en `warnings` y el día NO se dibuja, en vez de
    pintar una caída que solo existe en la base.
    """
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    series = performance_service.compute_series(db, portfolio, days=days)

    return PerformanceResponse(
        portfolio_id=portfolio.id,
        base_currency=portfolio.base_currency,
        as_of=dt.datetime.now(dt.UTC),
        points=[
            ValuePointRead(
                date=point.date,
                market_value=point.market_value,
                cash=point.cash,
                total_value=point.total_value,
                net_invested=point.net_invested,
                benchmark_value=point.benchmark_value,
            )
            for point in series.points
        ],
        benchmark_symbol=series.benchmark_symbol,
        # Se publica en PORCENTAJE y no en fracción: el resto de la API ya usa
        # `_pct` para todo lo porcentual, y mezclar 0.12 con 12.0 en la misma
        # respuesta es una invitación a multiplicar por cien dos veces.
        xirr_pct=None if series.xirr is None else round(series.xirr * 100, 2),
        benchmark_xirr_pct=(
            None if series.benchmark_xirr is None
            else round(series.benchmark_xirr * 100, 2)
        ),
        warnings=series.warnings,
    )
