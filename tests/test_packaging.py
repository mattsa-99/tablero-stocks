"""El paquete tiene que declarar lo que importa.

POR QUÉ ESTE TEST EXISTE
========================
Durante un tiempo `apscheduler` funcionó solo porque estaba instalado a mano
en un entorno de pruebas, y `httpx` porque vivía en el extra `dev` mientras el
código de producción ya lo importaba. Las dos cosas fallan igual: en una
instalación limpia, y solo al ejecutar la ruta que usa la dependencia. El
planificador arranca en el lifespan y el cliente de Banrep en el primer
refresco de tasas, así que ninguna de las dos habría aparecido al abrir la
página.

Un `pip install -e .` en una máquina limpia tiene que bastar.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Módulos del propio proyecto. No son dependencias.
PROPIOS = {"app", "mcp_server", "scripts", "tests", "alembic"}

# Nombre del import -> nombre en PyPI, cuando difieren.
DISTRIBUCION = {
    "pydantic_settings": "pydantic-settings",
    "dateutil": "python-dateutil",
}


def _imports_de(rutas: list[Path]) -> set[str]:
    encontrados: set[str] = set()
    for archivo in rutas:
        arbol = ast.parse(archivo.read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                encontrados |= {a.name.split(".")[0] for a in nodo.names}
            # `level > 0` es un import relativo: nunca es una dependencia.
            elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
                encontrados.add(nodo.module.split(".")[0])
    return encontrados - PROPIOS - set(sys.stdlib_module_names)


def _declaradas(seccion: str | None = None) -> set[str]:
    datos = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))
    proyecto = datos["project"]
    crudas = (
        proyecto["dependencies"]
        if seccion is None
        else proyecto["optional-dependencies"][seccion]
    )
    nombres = set()
    for linea in crudas:
        # "uvicorn[standard]>=0.32" -> "uvicorn"
        nombre = linea.split(">=")[0].split("==")[0].split("[")[0].strip()
        nombres.add(nombre.lower().replace("-", "_"))
    return nombres


def test_every_runtime_import_is_declared():
    """Lo que `app/` importa tiene que estar en `dependencies`.

    No en `dev`, no instalado a mano: en las dependencias de verdad. El fallo
    que esto evita solo aparece en una instalación limpia y solo al ejecutar
    la ruta afectada, que es la forma más cara de descubrirlo.
    """
    usados = _imports_de(sorted((RAIZ / "app").rglob("*.py")))
    declarados = _declaradas()

    faltan = {
        modulo
        for modulo in usados
        if modulo.lower().replace("-", "_") not in declarados
        and DISTRIBUCION.get(modulo, modulo).lower().replace("-", "_") not in declarados
    }
    assert not faltan, (
        f"`app/` importa {sorted(faltan)} y pyproject.toml no lo declara. "
        f"Funciona en tu máquina porque ya está instalado; en una limpia, no."
    )


def test_the_optional_extra_is_really_optional():
    """El tablero funciona entero sin el asesor MCP.

    Si una dependencia del extra se colara en `app/`, el extra dejaría de ser
    opcional sin que nadie lo notara hasta una instalación sin él.
    """
    usados = _imports_de(sorted((RAIZ / "app").rglob("*.py")))
    assert "mcp" not in usados, "`app/` no puede depender del asesor"


def test_the_mcp_server_declares_its_own_dependency():
    """Y al revés: `mcp_server/` sí puede usarla, y por eso existe el extra."""
    servidor = RAIZ / "mcp_server"
    if not servidor.exists():
        return
    usados = _imports_de(sorted(servidor.rglob("*.py")))
    assert "mcp" in usados
    assert "mcp" in _declaradas("mcp")
