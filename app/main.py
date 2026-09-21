from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from os import PathLike
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from app.core import scheduler
from app.core.config import settings
from app.core.exceptions import (
    DuplicateError,
    InsufficientUniverse,
    InvalidAllocationPlan,
    InvalidJournalEntry,
    InvalidLedgerOperation,
    NotFoundError,
    ProviderError,
    SymbolNotFound,
)
from app.db import registry  # noqa: F401  -- registra los modelos
from app.routers import (
    allocation,
    fixed_income,
    journal,
    market,
    market_data,
    opportunities,
    performance,
    portfolios,
    suggestion,
    transactions,
    views,
)

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    scheduler.start_scheduler()

    # La recuperación va en una tarea aparte: si la última sincronización es
    # antigua puede tardar minutos, y el arranque de la API no debe esperarla.
    catchup: asyncio.Task | None = None
    if settings.enable_background_refresh:
        catchup = asyncio.create_task(scheduler.run_catchup_in_background())
    try:
        yield
    finally:
        if catchup is not None:
            catchup.cancel()
            with suppress(asyncio.CancelledError):
                await catchup
        scheduler.shutdown_scheduler()


app = FastAPI(
    title="Tablero de Inversiones",
    description=(
        "API de gestión de portafolios, cálculo de rendimiento y detección de "
        "oportunidades. Los scores NO son recomendaciones de inversión."
    ),
    version="0.2.0",
    lifespan=lifespan,
)


# ----------------------------------------------------------------------
# Compresión. El JSON de oportunidades es MUY repetitivo -las mismas claves de
# `inputs` y `signals` repetidas una vez por candidato-, así que comprime
# mucho mejor que un payload normal. Medido sobre el universo real de 494
# activos:
#
#     limit= 10:   20,9 KB  ->   3,8 KB  (5,5x)
#     limit= 50:   99,2 KB  ->  11,9 KB  (8,3x)
#     limit=200:  390,9 KB  ->  41,2 KB  (9,5x)
#
# El umbral de 500 bytes deja fuera las respuestas pequeñas (un POST de
# transacción, el /api/health), donde comprimir cuesta más CPU de lo que
# ahorra en red.
# ----------------------------------------------------------------------
app.add_middleware(GZipMiddleware, minimum_size=500)


# ----------------------------------------------------------------------
# Traducción de errores de dominio a HTTP.
# Es el ÚNICO punto donde el dominio toca el transporte: los services no
# importan FastAPI y por eso son testeables sin cliente HTTP.
# ----------------------------------------------------------------------

_STATUS_BY_EXCEPTION: list[tuple[type[Exception], int]] = [
    (NotFoundError, 404),
    (DuplicateError, 409),
    (InvalidLedgerOperation, 422),
    (InvalidAllocationPlan, 422),
    (InvalidJournalEntry, 422),
    (InsufficientUniverse, 422),
    (SymbolNotFound, 422),
]


def _register_handler(exception_type: type[Exception], status_code: int) -> None:
    @app.exception_handler(exception_type)
    async def handler(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
        return JSONResponse(
            status_code=status_code,
            content={"detail": str(exc), "type": type(exc).__name__},
        )


for exception_type, status_code in _STATUS_BY_EXCEPTION:
    _register_handler(exception_type, status_code)


@app.exception_handler(ProviderError)
async def provider_error_handler(request: Request, exc: ProviderError) -> JSONResponse:
    """503 solo para fallos del proveedor que llegan sin absorber.

    Los caminos de lectura los capturan antes y los degradan a avisos, así que
    llegar aquí significa que la petición pedía explícitamente datos frescos.
    """
    logger.warning("Fallo del proveedor sin absorber: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": str(exc), "type": type(exc).__name__},
    )


class VersionedStaticFiles(StaticFiles):
    """Estáticos con caché larga SOLO cuando la URL viene versionada.

    `static_url()` ya pone `?v=<mtime>` en cada referencia, pero faltaba la
    otra mitad: `StaticFiles` manda `etag` y `last-modified` y NINGÚN
    `Cache-Control`, así que el navegador revalidaba en cada carga. Eran nueve
    peticiones 304 por navegación para archivos que no habían cambiado.

    Con la URL versionada, `immutable` es seguro por construcción: si el
    archivo cambia, cambia su mtime, cambia la URL y el navegador no tiene
    ninguna copia de ella. Es justo lo contrario del fallo que `static_url`
    vino a resolver -HTML nuevo con JavaScript viejo-, porque aquí el HTML no
    puede pedir la versión antigua.

    Sin `?v=` NO se cachea agresivamente: una petición directa a
    /static/js/store.js no lleva ninguna promesa de unicidad, y grabarla un año
    en el navegador del usuario sería imposible de deshacer.
    """

    def file_response(
        self,
        full_path: PathLike,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if b"v=" in scope.get("query_string", b""):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
        return response


app.mount(
    "/static",
    VersionedStaticFiles(directory=str(Path(__file__).resolve().parent / "static")),
    name="static",
)

app.include_router(portfolios.router)
app.include_router(transactions.router)
app.include_router(opportunities.router)
app.include_router(journal.router)
app.include_router(allocation.router)
app.include_router(fixed_income.router)
app.include_router(performance.router)
app.include_router(market.router)
app.include_router(market_data.router)
app.include_router(suggestion.router)
# El último: sus rutas de página no deben ensombrecer ningún prefijo /api.
app.include_router(views.router)


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": app.version}
