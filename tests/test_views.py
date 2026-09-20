"""Tests de la capa de presentación.

No se testea el comportamiento de Alpine -eso requiere un navegador- sino el
contrato entre backend y frontend: que las páginas se sirvan, que los estáticos
existan y que el HTML referencie los identificadores que el JS espera. Un
`x-ref` renombrado en la plantilla y no en el JS rompe el gráfico sin que nada
falle en el servidor.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Las fixtures `client`, `provider` y `portfolio_id` viven en conftest.py:
# pytest las resuelve por nombre, sin importarlas.

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
STATIC_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js"


@pytest.mark.parametrize("path", ["/", "/oportunidades"])
def test_pages_render(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in response.text.lower()


@pytest.mark.parametrize(
    "path",
    [
        "/static/css/app.css",
        "/static/js/store.js",
        "/static/js/charts.js",
        "/static/js/portfolio.js",
        "/static/js/opportunities.js",
    ],
)
def test_static_assets_are_served(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.content


def test_dark_theme_is_the_default(client):
    assert 'data-theme="dark"' in client.get("/").text


def test_views_are_absent_from_the_api_schema(client):
    """La documentación de la API debe describir solo la API."""
    paths = client.get("/openapi.json").json()["paths"]
    assert "/" not in paths
    assert "/oportunidades" not in paths
    assert "/api/portfolios" in paths


def test_dashboard_wires_its_alpine_component(client):
    html = client.get("/").text
    assert 'x-data="portfolioView()"' in html
    # El JS busca este ref para montar el gráfico.
    assert 'x-ref="donut"' in html
    assert "/static/js/portfolio.js" in html


def test_opportunities_wires_its_alpine_component(client):
    html = client.get("/oportunidades").text
    assert 'x-data="opportunitiesView()"' in html
    assert "/static/js/opportunities.js" in html


def test_alpine_loads_after_the_component_definitions(client):
    """El orden importa: los componentes se registran en `alpine:init`.

    Si el core de Alpine se evaluara antes que store.js, el store no existiría
    para los componentes ya montados y las vistas quedarían vacías.
    """
    html = client.get("/").text
    assert html.index("/static/js/store.js") < html.index("/static/vendor/alpine.min.js")
    assert html.index("/static/js/portfolio.js") < html.index("/static/vendor/alpine.min.js")


def test_no_nested_alpine_templates():
    """`<template x-if>` no puede envolver a `<template x-for>`.

    Alpine clona `template.content.firstElementChild` para x-if; si ese hijo es
    otro template, el bucle interior nunca se inicializa y la sección se
    renderiza VACÍA sin ningún error en consola. Es el bug más silencioso de
    esta capa, así que se vigila estructuralmente.
    """
    templates = TEMPLATES.glob("*.html")
    offenders = []
    for path in templates:
        html = path.read_text()
        for match in re.finditer(r"<template[^>]*x-if[^>]*>\s*<template", html):
            line = html[: match.start()].count("\n") + 1
            offenders.append(f"{path.name}:{line}")
    assert not offenders, f"Templates de Alpine anidados en: {offenders}"


def test_the_page_loads_nothing_from_a_cdn():
    """Sin red, la página tiene que seguir siendo usable.

    Antes Tailwind, Alpine y Chart.js venían de fuera, así que un corte de red
    la dejaba sin estilos y sin interactividad. Es justo lo contrario de lo que
    hace el backend, que conserva el último valor y avisa en vez de romperse.
    """
    html = (TEMPLATES / "base.html").read_text()
    assert "//cdn" not in html
    assert "https://" not in html.split("<body")[0]


def test_the_vendored_libraries_are_present_and_documented():
    """Vendorizar congela la versión de verdad; el README dice cuál es.

    La URL fijaba la versión pero no lo que el CDN servía bajo ella. Ahora el
    archivo es el contrato, y este test impide que se borre uno o que la tabla
    de versiones se quede sin actualizar.
    """
    vendor = TEMPLATES.parent / "static" / "vendor"
    readme = (vendor / "README.md").read_text()
    for name in ("alpine.min.js", "alpine-collapse.min.js", "chart.umd.min.js"):
        assert (vendor / name).stat().st_size > 1000, f"{name} falta o está vacío"
        assert name in readme, f"{name} no aparece en la tabla de versiones"


def test_the_collapse_plugin_loads_before_alpine_core():
    """Se registra sobre `window.Alpine` al arrancar: después no se registraría."""
    html = (TEMPLATES / "base.html").read_text()
    assert html.index("alpine-collapse.min.js") < html.index("vendor/alpine.min.js")


def test_decimal_fields_arrive_as_strings(client, portfolio_id):
    """Contrato que obliga al helper `num()` del frontend.

    Pydantic serializa Decimal como string para no perder exactitud. Si algún
    día pasaran a número, `num()` seguiría funcionando; lo que rompería el
    frontend es lo contrario -asumir número y recibir string-, así que se fija
    aquí el comportamiento real.
    """
    summary = client.get(f"/api/portfolios/{portfolio_id}").json()
    for field in ("total_cost", "cash_balance", "realized_pnl"):
        assert isinstance(summary[field], str), f"{field} debería llegar como string"


# --------------------------------------------------------------------------
# Combobox de símbolos
# --------------------------------------------------------------------------


def test_both_forms_use_the_symbol_combobox(client):
    """El input de texto libre se sustituyó en LOS DOS formularios.

    Si uno se queda con el input plano, el usuario sigue teniendo que conocer
    el ticker exacto justo en el formulario que más se usa.
    """
    html = client.get("/").text
    assert html.count('x-data="symbolCombobox(') == 2
    assert 'id="sim-symbol"' in html
    assert 'id="tx-symbol"' in html
    # El input de texto plano ya no debe existir en ninguno.
    assert 'placeholder="AAPL"' not in html
    assert 'placeholder="NVDA"' not in html


def test_combobox_declares_its_aria_contract(client):
    """El combobox se maneja con teclado; sin estos atributos es inaccesible."""
    html = client.get("/").text
    for attribute in (
        'role="combobox"',
        'aria-autocomplete="list"',
        ':aria-expanded=',
        ':aria-activedescendant=',
        'role="listbox"',
        'role="option"',
        ':aria-selected=',
    ):
        assert attribute in html, f"falta {attribute}"


def test_combobox_option_ids_are_scoped_per_instance(client):
    """Dos instancias en la misma página no pueden compartir ids de opción:
    aria-activedescendant apuntaría al elemento equivocado."""
    html = client.get("/").text
    assert 'id="sim-symbol-listbox"' in html
    assert 'id="tx-symbol-listbox"' in html


def test_combobox_script_is_loaded_before_alpine(client):
    html = client.get("/").text
    assert html.index("/static/js/combobox.js") < html.index("/static/vendor/alpine.min.js")


def test_only_the_simulator_prefills_the_price(client):
    """La distinción que evita corromper el coste base.

    En simulación el precio se prellena: siempre es «si lo hiciera ahora».
    En el alta real NO, porque la fecha puede ser pasada y el precio de hoy
    sería un dato falso que queda congelado en el coste medio para siempre.
    """
    simulation = (STATIC_JS / "simulation.js").read_text()
    transactions = (STATIC_JS / "portfolio.js").read_text()

    assert "this.draft.price = window.fmt.priceForInput" in simulation, (
        "El simulador debe prellenar el precio"
    )
    assert "this.form.price = window.fmt.priceForInput(this.marketPrice)" in transactions
    # En el alta, la única ASIGNACIÓN del precio está dentro de useMarketPrice,
    # nunca en applySymbol. Se busca la asignación y no la subcadena suelta:
    # el comentario de applySymbol menciona `form.price` justo para explicar
    # por qué no lo toca, y una comprobación ingenua lo confundiría con código.
    apply_block = transactions[
        transactions.index("applySymbol(suggestion)") : transactions.index("useMarketPrice()")
    ]
    assert "this.form.price =" not in apply_block, (
        "applySymbol NO puede rellenar el precio en una transacción real"
    )


def test_both_forms_offer_the_market_price(client):
    html = client.get("/").text
    assert html.count("useMarketPrice()") == 2
    assert "Precio de mercado:" in html
    assert "vs mercado" in html, "El simulador muestra la distancia con el mercado"


def test_price_helper_lives_in_one_place():
    """Estaba duplicado en dos archivos que cargan en la misma página."""
    definitions = sum(
        (STATIC_JS / name).read_text().count("function priceForInput")
        for name in ("store.js", "simulation.js", "portfolio.js", "combobox.js")
    )
    assert definitions == 1


# --------------------------------------------------------------------------
# Versionado de estáticos
# --------------------------------------------------------------------------


def test_static_assets_carry_a_version_in_the_url(client):
    """Sin versión, el navegador sirve JS viejo con HTML nuevo.

    `StaticFiles` manda `etag` y `last-modified` pero NO `Cache-Control`, así
    que el navegador aplica frescura heurística y puede no llegar a preguntar
    si el archivo cambió. Como la plantilla SÍ se renderiza en cada petición,
    el resultado es HTML nuevo con JavaScript viejo: las expresiones de Alpine
    que apuntan a métodos que aún no existen no lanzan nada visible y la
    sección se queda vacía. Pasó de verdad con los chips de mercado.
    """
    import re

    for page in ("/", "/oportunidades"):
        html = client.get(page).text
        referenced = re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
        assert referenced, f"{page} no referencia ningún estático"

        unversioned = [url for url in referenced if "?v=" not in url]
        assert not unversioned, (
            f"{page} sirve estáticos sin versión: {unversioned}. "
            "Usa static_url() en la plantilla."
        )


def test_the_version_changes_when_the_file_changes(tmp_path, monkeypatch):
    """La versión es la marca de tiempo: si no cambiara, no serviría de nada."""
    from app.routers import views

    asset = tmp_path / "static" / "js" / "prueba.js"
    asset.parent.mkdir(parents=True)
    asset.write_text("uno")
    monkeypatch.setattr(views, "BASE_DIR", tmp_path)

    first = views.static_url("/static/js/prueba.js")
    assert "?v=" in first

    import os

    stat = asset.stat()
    os.utime(asset, (stat.st_atime, stat.st_mtime + 60))
    assert views.static_url("/static/js/prueba.js") != first


def test_a_missing_static_file_does_not_break_the_page(tmp_path, monkeypatch):
    """Un estático que falta es un problema, pero no debe impedir servir."""
    from app.routers import views

    monkeypatch.setattr(views, "BASE_DIR", tmp_path)
    assert views.static_url("/static/js/no-existe.js") == "/static/js/no-existe.js"
