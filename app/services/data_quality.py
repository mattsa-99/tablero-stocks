"""Filtro de datos rotos del proveedor, antes de que entren al scoring.

EL FALLO QUE ESTE MÓDULO EXISTE PARA PARAR
==========================================
Yahoo calcula los ratios que mezclan PRECIO con CONTABILIDAD POR ACCIÓN sin
convertir la divisa. Cuando una empresa cotiza en una divisa y reporta en
otra, el resultado es un número plausible y falso:

    CIB          cotiza USD, reporta COP  ->  price_to_book = 0,0022
    MINEROS.CL   cotiza COP, reporta USD  ->  price_to_book = 9.267

El error va en las dos direcciones y no lanza nada. Medido: con el 0,0022 de
CIB, su factor de valoración sale 94,6 y encabeza el ranking; con un valor
plausible baja a 84,7 y cae del puesto 1 al 4. Es decir, un dato roto decide
qué se le recomienda comprar al usuario.

EL P/E NO ESTÁ AFECTADO, Y ESO SE COMPROBÓ
==========================================
Yahoo SÍ convierte el beneficio por acción a la divisa de cotización antes de
dividir. Verificado sobre CIB, MINEROS.CL, TM, ASML y AAPL: `precio / eps`
coincide exactamente con el `trailingPE` que devuelve. Por eso el P/E se
conserva -y por eso se puede recalcular con el precio fresco, ver
`fresh_trailing_pe`-, mientras que P/B, P/S y EV/EBITDA se descartan.

DOS REDES, PORQUE UNA SOLA NO BASTA
===================================
1. Divisas distintas: caza el caso general y es exacto, porque es la causa.
2. Magnitud implausible: caza lo que la primera no ve. `BRK-B` cotiza y
   reporta en USD, y aun así da 0,001 porque compara el precio de la clase B
   con el valor contable de la clase A.

DESCARTAR, NO CORREGIR
======================
Se probó reescalar por el tipo de cambio y no es fiable: para CIB da 6,97,
que sigue siendo implausible para un banco porque un ADR representa varias
acciones locales y esa proporción no viene en los datos. Un valor ausente
hace que el motor promedie los sub-factores que sí tiene; un valor corregido
a medias afirmaría algo falso con aire de precisión.
"""

from __future__ import annotations

from dataclasses import dataclass

# Ratios que dividen el PRECIO entre una magnitud contable POR ACCIÓN. Son los
# que Yahoo no convierte. `trailing_pe` y `forward_pe` quedan fuera a
# propósito: ahí sí convierte, y comprobarlo está en los tests.
PRICE_TO_BOOK_RATIOS: tuple[str, ...] = (
    "price_to_book",
    "price_to_sales",
    "ev_to_ebitda",
)

# Bandas de magnitud plausible. Deliberadamente ANCHAS: solo tienen que cazar
# lo que la comprobación de divisa no ve, y un falso positivo descarta un dato
# bueno. Colgate tiene un P/B real de ~303 -por recompras que dejan el
# patrimonio contable en casi nada- y debe pasar; los 9.267 de MINEROS.CL, no.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "price_to_book": (0.01, 1_000.0),
    "price_to_sales": (0.005, 1_000.0),
    "ev_to_ebitda": (0.1, 1_000.0),
}


@dataclass
class SanitisedFundamentals:
    """Qué se descartó y por qué, para poder decirlo en el informe de sync."""

    values: dict[str, float | None]
    dropped: dict[str, str]

    @property
    def has_drops(self) -> bool:
        return bool(self.dropped)


def sanitise_price_ratios(
    values: dict[str, float | None],
    *,
    quote_currency: str | None,
    financial_currency: str | None,
) -> SanitisedFundamentals:
    """Anula los ratios precio/contabilidad que no son comparables.

    `values` son los ratios tal como llegan del proveedor. Se devuelve una
    copia con los sospechosos en None y el motivo de cada descarte.
    """
    clean = dict(values)
    dropped: dict[str, str] = {}

    quote = (quote_currency or "").upper()
    financial = (financial_currency or "").upper()
    currencies_differ = bool(quote and financial and quote != financial)

    for ratio in PRICE_TO_BOOK_RATIOS:
        value = clean.get(ratio)
        if value is None:
            continue

        if currencies_differ:
            clean[ratio] = None
            dropped[ratio] = (
                f"cotiza en {quote} y reporta en {financial}: el proveedor no "
                f"convierte la divisa en este ratio"
            )
            continue

        low, high = PLAUSIBLE_RANGES[ratio]
        if not (low <= value <= high):
            clean[ratio] = None
            dropped[ratio] = (
                f"{value:g} fuera del rango plausible [{low:g}, {high:g}]"
            )

    return SanitisedFundamentals(values=clean, dropped=dropped)


def fresh_trailing_pe(
    price: float | None, eps_trailing: float | None
) -> float | None:
    """P/E con el precio de AHORA en vez del que tenía el proveedor al medirlo.

    El P/E es precio entre beneficio, así que se mueve cada día aunque el
    beneficio solo cambie cada trimestre. Guardarlo tal cual deja el pilar de
    valoración -el 35% del score- apoyado en un precio de hasta 24 h antes,
    mientras la cotización se refresca cada 15 minutos.

    Medido sobre 263 activos: desviación media del 1,6% y máxima del 7,6%,
    que mueve a un candidato 1,7 puestos de media y 11 en el peor caso. Poco,
    pero recalcularlo no cuesta NI UNA llamada de red: `eps_trailing` ya está
    guardado y el precio también.

    Devuelve None si falta algún dato o si el beneficio no es positivo: un
    P/E negativo no es "barato", es una empresa en pérdidas, y el motor ya lo
    descarta aguas abajo.
    """
    if price is None or eps_trailing is None:
        return None
    if price <= 0 or eps_trailing <= 0:
        return None
    return price / eps_trailing
