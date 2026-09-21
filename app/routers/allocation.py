"""API del plan de asignación entre clases de activo."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.repositories import portfolio as portfolio_repo
from app.routers.dependencies import DbSession
from app.schemas.allocation import (
    AllocationPlanWrite,
    AllocationReportRead,
    ClassPositionRead,
)
from app.services import allocation as allocation_service
from app.services import portfolio as portfolio_service

router = APIRouter(prefix="/api/allocation", tags=["allocation"])


def _build(db, portfolio, contribution) -> AllocationReportRead:
    summary = portfolio_service.get_summary(db, portfolio)
    report = allocation_service.build_report(db, portfolio, summary)
    orden = [
        p.asset_class
        for p in allocation_service.suggest_contribution(report, contribution)
    ]
    return AllocationReportRead(
        portfolio_id=portfolio.id,
        base_currency=portfolio.base_currency,
        has_plan=report.has_plan,
        planned_pct=report.planned_pct,
        total_value=report.total_value,
        positions=[ClassPositionRead(**vars(p)) for p in report.positions],
        contribution_order=orden,
        warnings=report.warnings,
    )


@router.get("", response_model=AllocationReportRead)
def get_allocation(
    db: DbSession,
    portfolio_id: Annotated[int, Query()],
    contribution: Annotated[
        float,
        Query(
            ge=0,
            description=(
                "Aporte nuevo que se piensa hacer, en divisa base. Ordena las "
                "clases por cuánto les falta; no propone vender"
            ),
        ),
    ] = 0.0,
) -> AllocationReportRead:
    """Peso actual de cada clase frente al plan declarado.

    Sin plan, devuelve solo el reparto actual: sigue siendo útil -dice dónde
    estás- y deja claro que no hay nada contra lo que compararlo.
    """
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    from decimal import Decimal

    return _build(db, portfolio, Decimal(str(contribution)))


@router.put("", response_model=AllocationReportRead)
def set_allocation(
    db: DbSession, portfolio_id: Annotated[int, Query()], payload: AllocationPlanWrite
) -> AllocationReportRead:
    """Reemplaza el plan ENTERO. Todo o nada, con la suma comprobada.

    Se reemplaza en vez de fusionar porque un plan es un reparto: enviar solo
    una clase y dejar las demás como estaban produciría sumas que el usuario
    no eligió.
    """
    from decimal import Decimal

    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    allocation_service.set_targets(
        db,
        portfolio,
        {t.asset_class: (t.target_pct, t.band_pct) for t in payload.targets},
    )
    for target in allocation_service.get_targets(db, portfolio):
        nota = next(
            (t.notes for t in payload.targets if t.asset_class == target.asset_class),
            None,
        )
        target.notes = nota
    db.commit()
    return _build(db, portfolio, Decimal(0))
