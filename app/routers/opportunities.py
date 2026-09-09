from __future__ import annotations

from fastapi import APIRouter, Query

from app.models import Asset
from app.repositories import portfolio as portfolio_repo
from app.routers.dependencies import DbSession, MarketService, ProviderDep
from app.schemas.opportunity import OpportunityResponse
from app.services import ingestion as ingestion_service
from app.services import opportunities as opportunity_service
from app.services import universe as universe_service
from app.services.grading import parse_quality_tiers
from app.services.regions import parse_regions

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


@router.get("", response_model=OpportunityResponse)
def get_opportunities(
    db: DbSession,
    market: MarketService,
    provider: ProviderDep,
    portfolio_id: int = Query(..., description="Portafolio contra el que se diversifica"),
    limit: int = Query(
        10,
        ge=1,
        le=200,
        description=(
            "Cuántas filas devolver. El tope son 200 porque con cualquier "
            "filtro de mercado puesto ya se ve la región entera -la mayor "
            "tiene 170 activos- y porque más allá la cuadrícula deja de "
            "poder recorrerse: 494 tarjetas son 65 pantallas de scroll. "
            "Puntuar cuesta lo mismo con cualquier límite (~165 ms): el "
            "universo completo SIEMPRE se evalúa, esto solo recorta lo que "
            "se muestra"
        ),
    ),
    symbols: str | None = Query(
        None, description="Candidatos extra separados por coma, ej. NVDA,KO,JNJ"
    ),
    refresh: bool = Query(
        True, description="Refrescar datos de los candidatos antes de puntuar"
    ),
    quality_tiers: str | None = Query(
        None,
        description=(
            "Calificaciones a mostrar, separadas por coma: muy_buena, buena, "
            "normal, mala, muy_mala, sin_calificar. Vacío = todas"
        ),
        examples=["muy_buena,buena"],
    ),
    regions: str | None = Query(
        None,
        description=(
            "Regiones a mostrar, separadas por coma: US, COL, LATAM, EU, "
            "ASIA, GLOBAL. Vacío = todas"
        ),
        examples=["US,COL"],
    ),
):
    """Ranking de oportunidades con el desglose completo de cada score.

    El universo son los activos activos del catálogo más los que se pasen en
    `symbols`. NO es una recomendación de inversión: es un ranking relativo
    dentro del universo evaluado, y la respuesta lo declara explícitamente.

    `quality_tiers` y `regions` filtran QUÉ SE MUESTRA, no qué se evalúa: el
    universo puntuado es siempre el completo, porque el score es un percentil
    y recalcularlo sobre un subconjunto cambiaría su significado. Un valor
    desconocido en cualquiera de los dos se ignora en lugar de devolver 422:
    son parámetros de interfaz, y fallar dejaría la vista en blanco.
    """
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)

    # El universo de INGESTA, no el catálogo entero. Con el catálogo semilla
    # cargado hay ~200 activos buscables de los que solo unas decenas tienen
    # datos; puntuarlos todos llenaría la respuesta de exclusiones por falta de
    # información. Solo se puede rankear lo que se ha medido.
    candidates: dict[str, Asset] = {
        a.symbol: a for a in universe_service.get_ingestion_universe(db)
    }

    warnings: list[str] = []
    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        for symbol in requested:
            candidates.setdefault(
                symbol, portfolio_repo.get_or_create_asset(db, symbol)
            )
        db.commit()

    assets = sorted(candidates.values(), key=lambda a: a.symbol)

    if refresh and assets:
        # Carga perezosa PRIMERO: un símbolo recién añadido con ?symbols= no
        # tiene histórico, y sin él sus factores de momentum y riesgo saldrían
        # imputados. Solo pide los que no tienen nada.
        warnings.extend(ingestion_service.ensure_data_for(db, provider, assets).warnings)
        # Después, el refresco normal por TTL de los que ya tenían datos.
        warnings.extend(market.full_refresh(assets, [portfolio.base_currency]).warnings)

    response = opportunity_service.compute_opportunities(
        db,
        portfolio,
        assets=assets,
        limit=limit,
        quality_tiers=parse_quality_tiers(quality_tiers),
        regions=parse_regions(regions),
    )
    response.warnings = [*warnings, *response.warnings]
    return response
