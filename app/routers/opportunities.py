from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query

from app.models import Asset
from app.repositories import portfolio as portfolio_repo
from app.routers.dependencies import DbSession, ProviderDep, SessionFactory
from app.schemas.ficha import FichaResponse, SizingRead
from app.schemas.opportunity import OpportunityResponse
from app.services import ficha as ficha_service
from app.services import ingestion as ingestion_service
from app.services import opportunities as opportunity_service
from app.services import refresh_jobs
from app.services import universe as universe_service
from app.services.grading import parse_quality_tiers
from app.services.regions import parse_regions

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


@router.get("", response_model=OpportunityResponse)
def get_opportunities(
    db: DbSession,
    provider: ProviderDep,
    session_factory: SessionFactory,
    background: BackgroundTasks,
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
        True,
        description=(
            "Lanzar un refresco de precios POR DETRÁS. Nunca retrasa esta "
            "respuesta: se puntúa con lo guardado y el campo `refreshing` "
            "dice si conviene volver a preguntar en unos segundos"
        ),
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
    asked_for: list[Asset] = []
    if symbols:
        requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        for symbol in requested:
            candidates.setdefault(
                symbol, portfolio_repo.get_or_create_asset(db, symbol)
            )
        db.commit()
        asked_for = [candidates[s] for s in requested if s in candidates]

    assets = sorted(candidates.values(), key=lambda a: a.symbol)

    # Carga perezosa EN LÍNEA, pero solo de lo que el usuario escribió.
    #
    # La distinción no es de rendimiento sino de qué está esperando quien
    # mira. Un símbolo tecleado en «candidatos extra» no tiene histórico: sin
    # él no se puede puntuar y desaparecería del ranking que se pidió a
    # propósito, o dejaría la vista en 422 si era el único. Son un puñado y
    # el usuario los está esperando, así que se traen ahora.
    #
    # El universo NO: ya lo ingestó el planificador, y en una instalación
    # recién creada serían los 494 símbolos otra vez dentro de la petición,
    # que es justo lo que este cambio elimina.
    if refresh and asked_for:
        warnings.extend(
            ingestion_service.ensure_data_for(db, provider, asked_for).warnings
        )

    # EL REFRESCO NO VA EN LA PETICIÓN. Antes aquí se llamaba a `full_refresh`
    # -el job de dos veces por semana- sobre los 494 símbolos: 269 s medidos
    # con la vista esperando, contra 0,23 s de puntuar lo ya guardado. Ahora
    # se encola para después de responder y el usuario ve el ranking al
    # instante; `refreshing` le dice al frontend que vuelva a preguntar.
    scope = f"opportunities:{portfolio.id}"
    if refresh and assets:
        background.add_task(
            refresh_jobs.refresh_quotes_for,
            session_factory,
            provider,
            [a.symbol for a in assets],
            portfolio.base_currency,
            scope=scope,
        )

    response = opportunity_service.compute_opportunities(
        db,
        portfolio,
        assets=assets,
        limit=limit,
        quality_tiers=parse_quality_tiers(quality_tiers),
        regions=parse_regions(regions),
    )
    # `refresh_running` y no "acabo de encolar una": entre encolar y ejecutar
    # hay un hueco, y si otra petición ya tenía el candado la nuestra no hará
    # nada. Preguntar por el candado describe lo que de verdad está pasando.
    response.refreshing = (refresh and bool(assets)) or refresh_jobs.refresh_running(scope)
    response.warnings = [*warnings, *response.warnings]
    return response


@router.get("/{symbol}/ficha", response_model=FichaResponse)
def get_ficha(
    symbol: str,
    db: DbSession,
    portfolio_id: int = Query(..., description="Portafolio contra el que se contextualiza"),
):
    """Ficha de compra de UNA empresa: score, banderas, salud, pares y cartera.

    Lee lo ya guardado y no llama al proveedor: es rápida y determinista. NO es
    una recomendación: el veredicto solo dice si el filtro encontró problemas.
    """
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    return ficha_service.build_ficha(db, portfolio, symbol)


@router.get("/{symbol}/sizing", response_model=SizingRead)
def get_sizing(
    symbol: str,
    db: DbSession,
    portfolio_id: Annotated[int, Query()],
    capital: Annotated[
        Decimal | None,
        Query(
            gt=0,
            description="Capital total en divisa base. Por defecto, el de la cartera "
            "(posiciones + efectivo positivo)",
        ),
    ] = None,
    risk_budget_pct: Annotated[
        float | None,
        Query(
            gt=0,
            le=20,
            description="Cuánto de la cartera aceptas perder por esta posición si "
            "repite su peor caída, en %",
        ),
    ] = None,
    max_position_pct: Annotated[
        float | None, Query(gt=0, le=100, description="Tope de peso por posición, en %")
    ] = None,
):
    """Cuánto poner en esta empresa con otros supuestos (recalcula sin rehacer la ficha)."""
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    return ficha_service.build_sizing(
        db, portfolio, symbol,
        capital=capital, risk_budget_pct=risk_budget_pct, max_position_pct=max_position_pct,
    )
