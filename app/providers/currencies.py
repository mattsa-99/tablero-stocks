"""Divisas cotizadas en SUBUNIDAD: la trampa de los peniques de Londres.

Algunas bolsas no cotizan en la divisa, sino en su centésima parte. Yahoo lo
señala cambiando la CAJA del código ISO, y solo eso:

    SHEL.L   currency="GBp"   price=3344.5    -> son 33,44 GBP, no 3.344,50
    TEVA.TA  currency="ILA"   price=...       -> agorot, no shekels
    NPN.JO   currency="ZAc"   price=...       -> centavos, no rands

Un `.upper()` sobre "GBp" produce "GBP", que es una divisa REAL y distinta.
El error no revienta en ninguna parte: se convierte en una posición valorada
100 veces de más, y multiplicada después por GBP/COP (~4.360) da un valor de
cartera absurdo que nada en el sistema puede detectar a posteriori.

Es especialmente grave aquí porque `transactions.fx_rate_to_base` CONGELA el
tipo aplicado: si se registra una compra con el precio inflado, corregir este
módulo más tarde no arregla el coste histórico ya guardado.

Por eso la normalización vive en la frontera del proveedor: aguas abajo nadie
debe volver a ver "GBp". Se devuelven siempre ISO-4217 en mayúsculas y precios
en la unidad principal.
"""

from __future__ import annotations

# Código crudo de Yahoo -> (ISO real, divisor a aplicar al precio).
# La clave distingue mayúsculas y minúsculas A PROPÓSITO: para "GBp" la caja es
# la única señal. Pero NO todas se distinguen por la caja -"USX" va en
# mayúsculas y tampoco es ISO-, así que la tabla es una enumeración explícita y
# no una regla. Ver el aviso de mantenimiento más abajo.
MINOR_UNIT_CURRENCIES: dict[str, tuple[str, float]] = {
    "GBp": ("GBP", 100.0),  # peniques (London Stock Exchange)
    "GBX": ("GBP", 100.0),  # alias de peniques que usan algunos feeds
    "ILA": ("ILS", 100.0),  # agorot (Tel Aviv)
    "ZAc": ("ZAR", 100.0),  # centavos (Johannesburgo)
    "USX": ("USD", 100.0),  # centavos de dólar: granos y blandos (ZC=F, KC=F)
}

# MANTENIMIENTO: esta tabla es una enumeración, y una enumeración se queda
# corta. Lo que impide que eso pase inadvertido no es la tabla sino
# `tests/test_catalog.py::test_catalog_currencies_are_known_iso_codes`: exige
# que toda divisa del catálogo esté en `CURRENCY_USD_MAGNITUDE`, así que una
# subunidad nueva aparece como un fallo de test y no como precios 100x.
# Así se descubrió "USX", que pasa por ISO -tres letras, mayúsculas- y no lo es.

# Sufijos de bolsas que PUEDEN cotizar en subunidad. Sirven para no gastar una
# llamada de sondeo por símbolo cuando el histórico no trae la divisa: fuera de
# estas bolsas el problema no existe.
#
# No se asume la subunidad por el sufijo: en la LSE conviven valores en GBp,
# en GBP y en USD, y entre los futuros el oro va en USD mientras el maíz va en
# USX. El sufijo decide A QUIÉN se pregunta, nunca la respuesta.
MINOR_UNIT_EXCHANGE_SUFFIXES: tuple[str, ...] = (".L", ".TA", ".JO", "=F")


def normalize_currency(raw: str | None) -> tuple[str | None, float]:
    """Traduce el código de divisa de Yahoo a (ISO, divisor del precio).

    Devuelve ``(None, 1.0)`` si no hay dato: la ausencia se propaga como
    ausencia, nunca como un USD por defecto que valoraría mal el activo.

    >>> normalize_currency("GBp")
    ('GBP', 100.0)
    >>> normalize_currency("GBP")
    ('GBP', 1.0)
    >>> normalize_currency(None)
    (None, 1.0)
    """
    if raw is None:
        return None, 1.0
    code = raw.strip()
    if not code:
        return None, 1.0
    if code in MINOR_UNIT_CURRENCIES:
        return MINOR_UNIT_CURRENCIES[code]
    return code.upper(), 1.0


def may_quote_in_minor_units(symbol: str) -> bool:
    """True si el símbolo cotiza en una bolsa donde la subunidad es posible."""
    return symbol.upper().endswith(MINOR_UNIT_EXCHANGE_SUFFIXES)


def scale(value: float | None, divisor: float) -> float | None:
    """Aplica el divisor conservando la ausencia. None NO es 0."""
    return None if value is None else value / divisor
