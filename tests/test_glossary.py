"""Centro de ayuda: presencia, integridad del contenido y cobertura.

El glosario es contenido, no lógica, así que su modo de fallo no es una
excepción sino una mentira: una métrica sin explicar, o peor, explicada con
un nombre que ya no existe en la aplicación.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY_JS = ROOT / "app" / "static" / "js" / "glossary.js"


def _entries() -> list[dict]:
    """Todas las fichas del glosario, extraídas del literal del JS."""
    source = GLOSSARY_JS.read_text(encoding="utf-8")
    return [
        {
            "term": term,
            "has_definition": "definition:" in block,
            "has_example": "example:" in block,
            "block": block,
        }
        for term, block in re.findall(
            r'term:\s*"([^"]+)",(.*?)(?=\n\s*\{\s*\n\s*term:|\n\s*\],)',
            source,
            re.S,
        )
    ]


def test_the_glossary_has_entries_for_both_views():
    source = GLOSSARY_JS.read_text(encoding="utf-8")
    assert "portfolio: {" in source
    assert "opportunities: {" in source
    assert len(_entries()) >= 20, "El glosario quedó demasiado corto"


def test_every_entry_has_a_definition_and_a_practical_example():
    """El ejemplo es obligatorio, no decorativo.

    Una definición sola deja al usuario entendiendo la frase y sin saber qué
    hacer con el número que tiene delante: el ejemplo es lo que convierte
    «desviación estándar anualizada» en «33,5% son oscilaciones altas».
    """
    incomplete = [
        e["term"]
        for e in _entries()
        if not (e["has_definition"] and e["has_example"])
    ]
    assert not incomplete, f"Fichas sin definición o sin ejemplo: {incomplete}"


# ----------------------------------------------------------------------
# Cobertura: lo que la aplicación muestra debe estar explicado
# ----------------------------------------------------------------------


def test_every_factor_input_shown_in_the_ui_is_explained():
    """Las «métricas observadas» de cada tarjeta salen de estas claves.

    Si alguien añade un sub-factor al motor y no lo documenta, aparece en la
    interfaz un nombre técnico crudo (`ev_to_ebitda`) sin ninguna explicación,
    y el glosario deja de cumplir su única función. Esta prueba lee las claves
    del servicio, no una lista copiada.
    """
    service = (ROOT / "app" / "services" / "opportunities.py").read_text(
        encoding="utf-8"
    )
    # Las claves de los diccionarios `inputs={...}` del cálculo.
    blocks = re.findall(r"inputs=\{(.*?)\}", service, re.S)
    keys = {k for block in blocks for k in re.findall(r'"(\w+)":', block)}
    assert keys, "No se encontró ninguna clave de inputs en el servicio"

    source = GLOSSARY_JS.read_text(encoding="utf-8")
    missing = sorted(k for k in keys if k not in source)
    assert not missing, (
        f"Sub-factores visibles en la interfaz y sin explicar: {missing}. "
        "Añádelos a GLOSSARY en glossary.js."
    )


def test_the_portfolio_summary_metrics_are_explained():
    """Las tarjetas de la cabecera del portafolio, una por una."""
    source = GLOSSARY_JS.read_text(encoding="utf-8").lower()
    for label in (
        "valor de mercado",
        "coste total",
        "coste medio",
        "p&l total",
        "p&l realizado",
        "p&l no realizado",
        "dividendos",
        "caja",
        "peso",
    ):
        assert label in source, f"Sin explicar en el glosario: {label}"


# ----------------------------------------------------------------------
# Honestidad del contenido
# ----------------------------------------------------------------------


def test_the_score_is_described_as_relative_never_as_a_verdict():
    """La advertencia que no puede desaparecer al refactorizar.

    El score es ordinal: dice quién es el mejor del conjunto, nunca si ese
    mejor es bueno. Un glosario que lo presentara como una nota absoluta
    reintroduciría justo el malentendido que la calificación A-E resuelve.
    """
    source = GLOSSARY_JS.read_text(encoding="utf-8")
    assert "RELATIVO" in source
    assert "no es una recomendación de inversión" in source.lower()


def test_metrics_whose_data_can_arrive_broken_carry_a_warning():
    """`price_to_book` llega mal en CIB y BRK-B, y el glosario lo dice.

    Yahoo divide el precio del ADR en dólares entre un valor contable en
    pesos, y produce 0,0022. Explicar «por debajo de 1 significa que cotiza
    bajo su valor contable» sin más enseñaría a leer un dato roto como una
    ganga histórica.
    """
    source = GLOSSARY_JS.read_text(encoding="utf-8")
    pb = source[source.index('term: "price_to_book"') :][:2000]
    assert "caveat:" in pb
    assert "0,0022" in pb or "0.0022" in pb
    assert "proveedor" in pb


def test_the_cash_can_go_negative_warning_survives():
    """Es deliberado, y sin decirlo parece un error de la aplicación."""
    source = GLOSSARY_JS.read_text(encoding="utf-8")
    caja = source[source.index('term: "Caja"') :][:1200]
    assert "deliberado" in caja


def test_average_cost_is_not_sold_as_a_tax_document():
    source = GLOSSARY_JS.read_text(encoding="utf-8")
    assert "declaración fiscal" in source


# ----------------------------------------------------------------------
# Integración con la página
# ----------------------------------------------------------------------


@pytest.mark.parametrize("page", ["/", "/oportunidades"])
def test_the_help_button_and_drawer_are_on_every_page(client, page):
    """El botón vive en la barra superior, que es compartida."""
    html = client.get(page).text
    assert 'aria-label="Abrir el glosario de métricas"' in html
    assert "open-help" in html
    assert 'x-data="helpDrawer()"' in html
    assert "glossary.js" in html


def test_the_drawer_script_is_versioned_like_the_rest(client):
    """Sin versión, un cambio en el glosario no llegaría al navegador."""
    html = client.get("/").text
    match = re.search(r'src="(/static/js/glossary\.js[^"]*)"', html)
    assert match, "glossary.js no está enlazado"
    assert "?v=" in match.group(1)


def test_the_drawer_does_not_nest_alpine_templates():
    """`<template x-if>` envolviendo a `<template x-for>` renderiza vacío.

    Alpine clona `firstElementChild`; si es otro template el bucle no se
    inicializa y la sección desaparece sin error en consola. Por eso los
    grupos filtrados se calculan en un getter del componente.
    """
    html = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")
    drawer = html[html.index('x-data="helpDrawer()"') :]
    nested = re.search(r"<template[^>]*x-if[^>]*>\s*<template", drawer)
    assert nested is None, "Hay un x-for anidado dentro de un x-if"


def test_the_glossary_is_valid_javascript_data():
    """El literal se puede parsear: una coma de más rompería el archivo entero."""
    import subprocess

    result = subprocess.run(
        ["node", "--check", str(GLOSSARY_JS)], capture_output=True, text=True
    )
    if result.returncode == 127 or "not found" in result.stderr:
        pytest.skip("node no está disponible")
    assert result.returncode == 0, result.stderr


def test_the_examples_are_consistent_with_the_scoring_formula():
    """El ejemplo del score debe cuadrar con la fórmula real.

    20 de base + 33,0 + 28,9 + 15,0 − 10,6 = 86,3. Si alguien retoca los
    pesos y no el ejemplo, el glosario enseña una aritmética que no sale.
    """
    from app.core.config import settings

    source = GLOSSARY_JS.read_text(encoding="utf-8")
    assert "86,3" in source
    # 20 de base + los cuatro aportes de la ficha real de CIB.
    assert round(sum([20.0, 33.0, 28.9, 15.0, -10.6]), 1) == 86.3

    # La base del ejemplo es la que calcula el motor: w_risk x 100. Si alguien
    # cambia el peso del riesgo, el "+20" del glosario deja de ser cierto.
    assert settings.opportunity_weight_risk * 100.0 == 20.0

    # Y los porcentajes que anuncia cada pilar son los pesos configurados.
    for label, weight in (
        ("35%", settings.opportunity_weight_value),
        ("30%", settings.opportunity_weight_momentum),
        ("15%", settings.opportunity_weight_diversification),
        ("20%", settings.opportunity_weight_risk),
    ):
        assert label in source, f"El glosario no menciona el peso {label}"
        assert round(weight * 100) == int(label.rstrip("%"))


def test_the_glossary_scope_matches_what_the_ui_actually_renders():
    """Fija POR QUÉ el test de arriba mira solo a `opportunities.py`.

    En la tarjeta se pintan dos cosas distintas y solo una lleva cifras:

    - `FactorDetail.inputs` (de `opportunities.py`) SÍ se pintan, una por una,
      con su nombre técnico. Por eso todas tienen que estar en el glosario.
    - `SignalDetail.inputs` (de `grading.py`) NO se pintan: de cada señal solo
      se muestran `label`, `points` y `detail`, que ya son texto en español.

    Si algún día se empiezan a pintar, este test falla y avisa de que hay que
    ampliar el alcance del anterior a `grading.py`, donde hoy viven claves sin
    documentar (`market_pe`, `debt_to_equity_x`...) que nadie ve.
    """
    template = (ROOT / "app" / "templates" / "opportunities.html").read_text(
        encoding="utf-8"
    )
    signals_block = template.split("ratedSignals(row)")[1].split("</template>")[0]
    assert "inputs" not in signals_block, (
        "Se están pintando los `inputs` de las señales: amplía "
        "test_every_factor_input_shown_in_the_ui_is_explained a grading.py"
    )
