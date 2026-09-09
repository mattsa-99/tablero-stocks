"""La guía operativa no puede describir botones que ya no existen.

`docs/GUIA.md` le dice al usuario dónde pulsar. Es documentación de proceso, y
su modo de fallo es peor que quedarse desactualizada: manda a buscar un botón
que se renombró, y quien la sigue concluye que el que está perdido es él.

Estas pruebas comparan lo que la guía AFIRMA contra las plantillas y el motor.
No revisan la redacción -eso no se testea-, solo que cada referencia concreta
siga siendo cierta.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs" / "GUIA.md"

# Fuentes donde puede vivir una etiqueta de interfaz.
UI_SOURCES = (
    ROOT / "app" / "templates" / "base.html",
    ROOT / "app" / "templates" / "dashboard.html",
    ROOT / "app" / "templates" / "opportunities.html",
    ROOT / "app" / "static" / "js" / "portfolio.js",
    ROOT / "app" / "static" / "js" / "simulation.js",
    ROOT / "app" / "static" / "js" / "opportunities.js",
)


def ui_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in UI_SOURCES)


def test_the_guide_exists_and_is_reachable_from_the_readme():
    """Una guía que nadie encuentra no sirve de nada."""
    assert GUIDE.exists()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/GUIA.md" in readme


# Botones y secciones que la guía manda pulsar o mirar. Si alguno se renombra,
# hay que renombrarlo también en la guía: por eso están aquí.
CITED_LABELS = [
    "Distribución",
    "Símbolo",
    "Sector",
    "Calidad",
    "Mercado",
    "Mostrar",
    "Sugerencia óptima",
    "Simular compra",
    "Aplicar de verdad",
    "Nueva transacción",
    "Métricas observadas",
    "Posiciones efectivas",
    "Mayor posición",
    "Sincronizar",
    "Limpiar filtros",
]


@pytest.mark.parametrize("label", CITED_LABELS)
def test_every_ui_label_the_guide_cites_still_exists(label):
    source = ui_text()
    assert label in source, (
        f"La guía manda al usuario a «{label}» y ya no está en la interfaz. "
        "Actualiza docs/GUIA.md."
    )
    assert label in GUIDE.read_text(encoding="utf-8")


# Claves de métricas que la guía explica cómo leer.
CITED_METRICS = [
    "trailing_pe",
    "sma50_over_sma200_minus_1",
    "annualized_volatility",
    "sector_weight",
]


@pytest.mark.parametrize("metric", CITED_METRICS)
def test_every_metric_the_guide_names_is_produced_by_the_engine(metric):
    """Si el motor deja de emitir una clave, la guía enseña a leer un fantasma."""
    service = (ROOT / "app" / "services" / "opportunities.py").read_text(
        encoding="utf-8"
    )
    assert f'"{metric}"' in service
    assert metric in GUIDE.read_text(encoding="utf-8")


def test_the_guide_quotes_the_real_formula_weights():
    """Los pesos de la guía deben ser los que el motor aplica de verdad."""
    from app.core.config import settings

    guide = GUIDE.read_text(encoding="utf-8")
    for weight, expected in (
        ("0,35", settings.opportunity_weight_value),
        ("0,30", settings.opportunity_weight_momentum),
        ("0,15", settings.opportunity_weight_diversification),
        ("0,20", settings.opportunity_weight_risk),
    ):
        assert weight in guide, f"La guía no menciona el peso {weight}"
        assert f"{expected:.2f}".replace(".", ",") == weight


def test_the_guide_does_not_promise_a_simulate_button_on_the_cards():
    """El traspaso al simulador SOLO existe desde «Sugerencia óptima».

    Es el error más fácil de cometer al escribir esta guía: dar por hecho que
    desde cualquier tarjeta del ranking se puede simular. No se puede, y el
    usuario se pasaría diez minutos buscando el botón.
    """
    handoff = (ROOT / "app" / "static" / "js" / "suggestion.js").read_text(
        encoding="utf-8"
    )
    assert "handoffToSimulator" in handoff

    guide = GUIDE.read_text(encoding="utf-8")
    assert "no hay botón de simular" in guide.lower()


# ----------------------------------------------------------------------
# Honestidad: las advertencias que no pueden desaparecer
# ----------------------------------------------------------------------


def prose() -> str:
    """La guía como texto corrido, en minúsculas y sin saltos de línea.

    Buscar frases literales en markdown es frágil: el ajuste de línea parte
    cualquier expresión y un `**` en medio la rompe. Aquí interesa que la
    advertencia ESTÉ, no cómo quedó maquetada.
    """
    raw = GUIDE.read_text(encoding="utf-8").lower().replace("*", "")
    return re.sub(r"\s+", " ", raw)


@pytest.mark.parametrize(
    "claim,needle",
    [
        ("el score no es una recomendación", "no es un consejo de compra"),
        ("los pesos no tienen backtest", "sin ningún backtest"),
        ("el score es relativo, no absoluto", "ranking relativo"),
        ("el primero puede ser malo", "aunque todos sean malos"),
        ("el universo es solo USD y COP", "solo activos en usd y cop"),
        ("los datos del proveedor pueden llegar rotos", "no son infalibles"),
        ("el ticker puede no ser el instrumento", "no ser el instrumento"),
        ("el coste medio no es fiscal", "no es una declaración fiscal"),
        ("el simulador no escribe nada", "no escribe nada"),
    ],
)
def test_the_guide_states_its_limits(claim, needle):
    """Una guía que enseña a decidir compras y omite sus límites es peligrosa.

    Cada una de estas advertencias existe porque el sistema puede engañar al
    usuario en ese punto concreto. Quitarlas al reescribir el texto sería
    convertir una herramienta honesta en una que promete de más.
    """
    assert needle in prose(), f"Falta la advertencia sobre: {claim}"


def test_the_guide_sends_definitions_to_the_glossary_instead_of_duplicating():
    """La guía enseña el PROCESO; el glosario define los términos.

    Duplicar las definiciones garantiza que una de las dos copias envejezca.
    """
    guide = GUIDE.read_text(encoding="utf-8")
    assert "glosario" in guide.lower()


def test_the_guide_does_not_invent_a_portfolio_volatility_metric():
    """El simulador NO reporta volatilidad, por mucho que suene razonable.

    Reporta diversificación (HHI), posiciones efectivas y mayor posición.
    Mandar al usuario a buscar una «volatilidad del portafolio» que no existe
    es exactamente el fallo que estas pruebas evitan.
    """
    schema = (ROOT / "app" / "schemas" / "simulation.py").read_text(encoding="utf-8")
    assert "diversification_index" in schema
    assert "volatility" not in schema

    guide = GUIDE.read_text(encoding="utf-8").lower()
    assert not re.search(r"volatilidad d(el|e la) (portafolio|cartera)", guide)
