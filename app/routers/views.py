"""Rutas que sirven HTML.

Separadas de los routers `/api/*` a propósito: estas devuelven páginas, no
datos, y no aparecen en el esquema OpenAPI (`include_in_schema=False`) para que
la documentación de la API siga siendo solo la API.

Las plantillas no reciben datos del portafolio: la página se sirve vacía y
Alpine la puebla vía fetch. Así el HTML es cacheable y el mismo endpoint sirve
para cualquier portafolio, sin renderizar dos veces la misma información.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def static_url(path: str) -> str:
    """URL de un estático con la marca de tiempo del archivo como versión.

    POR QUÉ HACE FALTA. `StaticFiles` envía `etag` y `last-modified` pero NO
    `Cache-Control`, y sin él el navegador aplica FRESCURA HEURÍSTICA: puede
    servir su copia sin llegar a preguntar si cambió.

    El fallo que produce es especialmente malo aquí porque no se parece a un
    fallo. La plantilla se renderiza en cada petición, así que el HTML llega
    NUEVO mientras el JavaScript sigue siendo el VIEJO. Las expresiones de
    Alpine que apuntan a métodos que aún no existen no lanzan nada visible:
    la sección se queda vacía. Pasó con los chips de mercado y el botón de
    limpiar filtros, que sencillamente no aparecían.

    Con la marca de tiempo en la URL, cambiar el archivo cambia la URL y el
    navegador no tiene ninguna copia de ella. Si el archivo no existe se
    devuelve la ruta tal cual: un estático que falta es un problema, pero no
    uno que deba impedir que la página se sirva.
    """
    relative = path.lstrip("/")
    target = BASE_DIR / relative
    try:
        version = int(target.stat().st_mtime)
    except OSError:
        return f"/{relative}"
    return f"/{relative}?v={version}"


templates.env.globals["static_url"] = static_url

router = APIRouter(tags=["views"], include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request, name="dashboard.html", context={"active_page": "/"}
    )


@router.get("/oportunidades", response_class=HTMLResponse)
def opportunities(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="opportunities.html",
        context={"active_page": "/oportunidades"},
    )


@router.get("/diario", response_class=HTMLResponse)
def journal(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="journal.html",
        context={"active_page": "/diario"},
    )
