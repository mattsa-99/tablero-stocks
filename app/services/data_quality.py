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

TERCERA RED: ESTO NO ES UNA EMPRESA
===================================
Las dos redes anteriores atrapan números IMPLAUSIBLES. No ven el caso en que
el número es perfectamente plausible y aun así no significa nada, porque
detrás no hay ni beneficios ni patrimonio contable. Medido en la base real:

    SJNK  (fondo de bonos basura)   trailingPE = 0,89   -> valoración 99,9/100
    TLT   (deuda pública 20+ años)  priceToBook = 0,54  -> valoración 97,5/100
    PHYS  (solo tiene oro)          trailingPE = 5,89   -> valoración 98,8/100

Los tres encabezaban o casi el factor de valoración, que pesa el 35% del
score. Ninguno de los tres tiene beneficios.

Que son falsos no es una opinión: el propio Yahoo se contradice. Para SJNK,
`funds_data.equity_holdings` devuelve `Price/Earnings = 0.0`; para TLT, el
`bookValue` de 148,87 con el que calcula ese 0,54 convive en la MISMA
respuesta con un `navPrice` de 80,92. Y el caso de PHYS es perverso: su
«beneficio por acción» de 5,51 es la revalorización del oro, así que cuanto
más sube el metal más barato parece el fondo.

De ahí la regla: un activo que no tiene empresas dentro no recibe ningún
múltiplo. Y un fondo que SÍ las tiene conserva el P/E -es la media ponderada
del P/E de su cartera, y se comprobó: Yahoo da 24,40 para IVV, que es donde
cotiza el S&P 500- pero pierde los ratios contra el valor en libros: el
«valor contable» de un fondo es su NAV, y por arbitraje precio ≈ NAV, así que
ese cociente vale ≈1 siempre. Cuando sale distinto está roto, y cuando sale
bien no informa de nada.

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

from app.services.asset_class import EARNINGS_BACKED, AssetClass

# Ratios que dividen el PRECIO entre una magnitud contable POR ACCIÓN. Son los
# que Yahoo no convierte. `trailing_pe` y `forward_pe` quedan fuera a
# propósito: ahí sí convierte, y comprobarlo está en los tests.
PRICE_TO_BOOK_RATIOS: tuple[str, ...] = (
    "price_to_book",
    "price_to_sales",
    "ev_to_ebitda",
)

# Todo lo que dice «cuánto cuesta esto en relación con lo que produce o con lo
# que vale». Un activo sin empresas dentro no puede tener ninguno.
VALUATION_MULTIPLES: tuple[str, ...] = ("trailing_pe", "forward_pe", *PRICE_TO_BOOK_RATIOS)

# El BENEFICIO POR ACCIÓN, del que el motor rehace el P/E con el precio de hoy
# (`fresh_trailing_pe`). Va en la misma red que los múltiplos y no es un
# detalle: anular `trailing_pe` sin anular esto no sirve de nada, porque el
# motor lo vuelve a calcular a partir de aquí y pisa el None.
#
# Lo descubrió PHYS. Con sus múltiplos ya descartados seguía apareciendo con
# una valoración de 99,1 sobre 100, porque su `eps_trailing` de 5,51 -que es
# la revalorización del oro, no un beneficio- sobrevivía al filtro y
# reconstruía el P/E en cada petición.
EARNINGS_INPUTS: tuple[str, ...] = ("eps_trailing",)

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


def sanitise_multiples(
    values: dict[str, float | None],
    *,
    quote_currency: str | None,
    financial_currency: str | None,
    asset_class: AssetClass,
    is_assumed: bool = False,
) -> SanitisedFundamentals:
    """Anula los múltiplos de valoración que no significan nada.

    `values` son los ratios tal como llegan del proveedor. Se devuelve una
    copia con los sospechosos en None y el motivo de cada descarte.

    Las tres redes se aplican en orden de generalidad: primero "esto no es una
    empresa", que invalida el múltiplo entero; luego "esto es un fondo de
    acciones", que invalida solo los contables; y por último las comprobaciones
    de divisa y magnitud, que son las que atrapan a una empresa de verdad con
    un dato roto.
    """
    clean = dict(values)
    dropped: dict[str, str] = {}

    # `is_assumed` entra aquí y no es un detalle: cuando el proveedor no
    # categoriza un fondo se le SUPONE de acciones para poder rankearlo, y
    # «lo más probable» no es base para conservar un múltiplo. Yahoo no
    # categoriza los fondos de la BVC, así que un ETF de TES conservaría un
    # «P/E» que no significa nada. Ver `Classification.is_earnings_backed`.
    if asset_class not in EARNINGS_BACKED or is_assumed:
        motivo = (
            f"la clase «{asset_class.value}» es un SUPUESTO y no un dato del "
            f"proveedor: no se puede afirmar que tenga beneficios"
            if is_assumed and asset_class in EARNINGS_BACKED
            else f"un activo de clase «{asset_class.value}» no tiene beneficios "
            f"ni patrimonio contable: el múltiplo no mide nada"
        )
        for ratio in (*VALUATION_MULTIPLES, *EARNINGS_INPUTS):
            if clean.get(ratio) is not None:
                clean[ratio] = None
                dropped[ratio] = motivo
        return SanitisedFundamentals(values=clean, dropped=dropped)

    if asset_class is AssetClass.FONDO_ACCIONES:
        # El P/E SÍ se conserva: es la media ponderada del P/E de la cartera
        # del fondo. Los contables no: el valor en libros de un fondo es su
        # NAV y el precio se le pega por arbitraje.
        for ratio in PRICE_TO_BOOK_RATIOS:
            if clean.get(ratio) is not None:
                clean[ratio] = None
                dropped[ratio] = (
                    "el valor contable de un fondo es su NAV y el precio lo sigue "
                    "por arbitraje: el cociente vale ~1 siempre y no informa"
                )
        return SanitisedFundamentals(values=clean, dropped=dropped)

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


def sanitise_price_ratios(
    values: dict[str, float | None],
    *,
    quote_currency: str | None,
    financial_currency: str | None,
) -> SanitisedFundamentals:
    """Las dos redes originales, sobre una EMPRESA. Ver `sanitise_multiples`."""
    return sanitise_multiples(
        values,
        quote_currency=quote_currency,
        financial_currency=financial_currency,
        asset_class=AssetClass.ACCION,
    )


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
