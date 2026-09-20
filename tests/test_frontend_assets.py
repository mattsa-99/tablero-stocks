"""El CSS compilado tiene que cubrir lo que las plantillas y el JS usan.

Al pasar del Play CDN a Tailwind compilado, el escáner deja de correr en el
navegador: lo que no esté en `tailwind.css` en el momento del build NO existe.
Y una clase ausente no lanza nada -el elemento se renderiza sin estilo-, que
es exactamente el tipo de fallo silencioso contra el que este repo ya se
protege con `static_url()`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"
JS = ROOT / "app" / "static" / "js"
COMPILED = ROOT / "app" / "static" / "css" / "tailwind.css"
APP_CSS = ROOT / "app" / "static" / "css" / "app.css"

# Prefijos que Tailwind genera. Se acotan a propósito: barrer TODO token que
# parezca una clase metería nombres propios de `app.css` y utilidades
# inventadas, y el test avisaría de fallos que no lo son.
TAILWIND_PREFIXES = (
    "text-", "bg-", "border", "ring-", "hover:", "grid-", "gap-", "p-", "px-",
    "py-", "w-", "h-", "min-", "max-", "rounded", "shadow", "font-", "opacity-",
    "sm:", "md:", "lg:", "focus", "disabled:", "cursor-",
)


def _escape(token: str) -> str:
    return re.sub(r"([./\[\]%:#()])", r"\\\1", token)


def _used_classes() -> set[str]:
    """Clases escritas en plantillas y en JavaScript."""
    found: set[str] = set()
    files = list(TEMPLATES.rglob("*.html")) + list(JS.glob("*.js"))
    for path in files:
        text = path.read_text()
        for match in re.finditer(r"""(?:class|:class)\s*=\s*(["'])(.*?)\1""", text, re.S):
            for token in re.split(r"""[\s'"`?+{}()\[\],]+""", match.group(2)):
                if token and not token.endswith(":"):
                    found.add(token.strip(":"))
        if path.suffix == ".js":
            # Las tablas de estilo del JS (GRADE_STYLE, CHIP_INACTIVE) guardan
            # la clase entera como literal justo para que el escáner la vea.
            for match in re.finditer(r"""["'`]([^"'`\n]{2,140})["'`]""", text):
                literal = match.group(1)
                if re.match(r"^(text|bg|border|ring|hover|opacity)", literal):
                    found.update(literal.split())
    return found


def test_every_class_used_is_present_in_the_compiled_css():
    """Detecta además un build OLVIDADO.

    Si alguien añade una clase a una plantilla y no ejecuta `npm run build:css`,
    esa clase no estará en el CSS y el elemento saldrá sin estilo sin que nada
    falle. Este test convierte ese silencio en un fallo.
    """
    css = COMPILED.read_text()
    app_css = APP_CSS.read_text()

    missing = sorted(
        token
        for token in _used_classes()
        if token.startswith(TAILWIND_PREFIXES)
        and "${" not in token
        and f".{_escape(token)}" not in css
        and f".{token}" not in app_css
    )
    assert not missing, (
        "Clases usadas que NO están en tailwind.css "
        f"(¿falta `npm run build:css`?): {missing}"
    )


@pytest.mark.parametrize(
    "token",
    ["bg-good/10", "bg-bad/10", "bg-warn/10", "bg-s1/10", "bg-bg/85", "ring-s1/30"],
)
def test_opacity_modifiers_on_theme_colors_are_generated(token: str):
    """Estas clases NO se generaban, y el fallo era invisible.

    Tailwind no puede componer un canal alfa sobre un color que es literalmente
    `var(--good)`: la utilidad sencillamente no se emite. Bajo el Play CDN regía
    la misma limitación, así que los tintes de los chips de calificación y el
    fondo translúcido del encabezado nunca llegaron a pintarse. La solución es
    `color-mix` en `tailwind.config.js`; este test impide que se pierda.
    """
    css = COMPILED.read_text()
    assert f".{_escape(token)}" in css
    assert "color-mix" in css


def test_the_tailwind_config_lives_outside_the_template():
    """La configuración se mudó a `tailwind.config.js` al compilar.

    Si volviera a `base.html` sería señal de que alguien reintrodujo el Play
    CDN, que es un compilador corriendo en el navegador del usuario.
    """
    html = (TEMPLATES / "base.html").read_text()
    assert (ROOT / "tailwind.config.js").exists()
    assert "tailwind.config =" not in html
    assert "cdn.tailwindcss.com" not in html


# ----------------------------------------------------------------------
# Estado de la vista en la URL
# ----------------------------------------------------------------------

OPPORTUNITIES_JS = (JS / "opportunities.js").read_text()


def test_the_limit_options_match_the_template():
    """Dos listas del mismo selector, en dos archivos, sin build que las una.

    `LIMIT_OPTIONS` valida el `?limit=` que llega por la URL. Si se quedara
    corta respecto al `<select>`, elegir una opción legítima y recargar la
    descartaría en silencio y volvería al valor por defecto.
    """
    declared = re.search(r"const LIMIT_OPTIONS = \[([^\]]+)\]", OPPORTUNITIES_JS)
    assert declared, "LIMIT_OPTIONS no encontrado"
    in_js = {int(n) for n in re.findall(r"\d+", declared.group(1))}

    html = (TEMPLATES / "opportunities.html").read_text()
    select = html.split('id="op-limit"')[1].split("</select>")[0]
    in_html = {int(n) for n in re.findall(r':value="(\d+)"', select)}

    assert in_js == in_html, f"JS {sorted(in_js)} != plantilla {sorted(in_html)}"


def test_the_url_reuses_the_api_parameter_names():
    """Sin tabla de equivalencias no hay nada que desincronizar.

    La cadena de consulta de la página se pega tal cual detrás de
    /api/opportunities y devuelve exactamente lo que se está viendo.
    """
    sync = OPPORTUNITIES_JS.split("syncUrl() {")[1].split("\n    },")[0]
    for name in ("symbols", "quality_tiers", "regions", "limit"):
        assert f'"{name}"' in sync, f"la URL no publica «{name}» con el nombre de la API"


def test_the_grade_slug_table_is_derived_not_duplicated():
    """Una tabla inversa escrita a mano se desincroniza al tocar la otra.

    El síntoma sería un enlace compartido que restaura un filtro equivocado,
    sin ningún error visible.
    """
    assert "Object.entries(GRADE_SLUG)" in OPPORTUNITIES_JS
