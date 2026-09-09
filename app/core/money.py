"""Utilidades de aritmética monetaria.

Regla del sistema, con una frontera explícita:

- Datos del usuario y aritmética contable -> ``Decimal`` exacto.
- Datos de mercado para análisis (precios, ratios) -> ``float``.
- La frontera entre ambos es ``to_decimal()``, que SIEMPRE pasa por ``str``.

``Decimal(0.1)`` arrastra la representación binaria completa
(0.1000000000000000055511151231257827); ``Decimal(str(0.1))`` da ``0.1``.
Esa diferencia, acumulada en un coste medio que se recalcula tras cada compra,
es lo que produce un P&L desviado en céntimos que el usuario no puede explicar.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

ZERO = Decimal("0")

# Escala de trabajo interna. NO se redondea durante el cálculo: solo al
# presentar. Redondear en cada paso intermedio del replay introduce sesgo.
MONEY_SCALE = Decimal("0.00000001")  # 8 decimales
PERCENT_SCALE = Decimal("0.0001")


def to_decimal(value: float | int | str | Decimal | None) -> Decimal | None:
    """Convierte a Decimal exacto cruzando la frontera float -> Decimal."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Valor no convertible a Decimal: {value!r}") from exc


def quantize_money(value: Decimal, scale: Decimal = MONEY_SCALE) -> Decimal:
    """Cuantiza para PRESENTAR. Nunca para almacenar cálculos intermedios."""
    return value.quantize(scale, rounding=ROUND_HALF_UP)


def safe_divide(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """División que devuelve None en vez de reventar con denominador cero.

    Un retorno porcentual sobre coste cero no es 0 ni infinito: es indefinido,
    y presentarlo como 0% sería mentir.
    """
    if denominator == ZERO:
        return None
    return numerator / denominator
