"""Renta fija directa: valorar un TES, un CDT o una FIC, y saber juzgarlo.

VALORACIÓN: COSTO MÁS DEVENGO, Y SE DECLARA SIEMPRE
===================================================
No hay precio de mercado. Un CDT es un contrato entre dos partes y nadie lo
cotiza; un TES sí se negocia, pero su precio lo publican proveedores de pago y
el propio Banco de la República advierte que su curva cero cupón «es de tipo
informativo y su fin no es la valoración de portafolios».

Así que se devenga:

    factor = (1 + tasa_efectiva_anual) ** (días transcurridos / 365)

Es lo que vale SI SE LLEVA A VENCIMIENTO. No es lo que alguien pagaría hoy: si
las tasas del mercado suben, un TES vale menos que esto; si bajan, más. La
diferencia puede ser grande —un TES a 10 años pierde en torno al 7% de su
valor por cada punto que suban las tasas— y por eso cada cifra que sale de
aquí va acompañada de la advertencia. Un número presentado como valor de
mercado cuando no lo es sería exactamente el tipo de dato plausible y falso
que el resto del sistema se dedica a filtrar.

CÓMO SE ENCAJA EN EL LEDGER SIN TOCARLO
=======================================
Una compra se registra como cualquier otra: `quantity` = el capital invertido
y `price` = 1. El devengo se publica como una COTIZACIÓN más, con
`source="devengo"`, así que el valor de la posición, la curva de rendimiento,
el reparto por clase y todo lo demás funcionan sin un solo caso especial.

El factor es un precio por unidad legítimo, no un apaño: con 10.000.000 de
pesos a un año al 12,3%, la unidad vale 1,123 y la posición 11.230.000.

NO SE CALIFICA NI SE PUNTÚA
===========================
No entra en el ranking. Un score es un rango percentil contra pares, y aquí no
hay pares medibles: cada CDT tiene su emisor, su plazo y su tasa. Lo que sí se
puede hacer, y es lo que importa de verdad, es contestar cinco preguntas con
datos oficiales: ¿le gana a la inflación?, ¿le gana a lo que paga el mercado?,
¿cuánto tiempo queda?, ¿puedo salir?, ¿quién me debe?
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Asset, AssetType, FixedIncomeTerms, RateKind
from app.services import reference_rates as rates_service

DAYS_IN_YEAR = Decimal("365")

# Índice de referencia de cada tipo de tasa indexada, y su plazo natural.
INDEX_SERIES: dict[RateKind, str] = {
    RateKind.IBR: "ibr_3m",
    RateKind.DTF: "dtf_90d",
    RateKind.IPC: "inflacion_anual",
}


@dataclass(frozen=True)
class Accrual:
    """El devengo de un instrumento a una fecha."""

    factor: Decimal
    effective_rate_pct: Decimal
    days_elapsed: int
    days_to_maturity: int
    is_matured: bool
    basis: str
    caveat: str


@dataclass
class FixedIncomeAssessment:
    """Las cinco preguntas, contestadas con datos oficiales y fechados."""

    symbol: str
    issuer: str
    currency: str
    rate_kind: str
    nominal_rate_pct: float
    net_rate_pct: float | None
    real_rate_pct: float | None
    inflation_pct: float | None
    inflation_as_of: dt.date | None
    market_reference_pct: float | None
    market_reference_label: str | None
    market_reference_as_of: dt.date | None
    beats_inflation: bool | None
    beats_market: bool | None
    days_to_maturity: int
    has_secondary_market: bool
    # Qué pagaron los bancos DE VERDAD a este plazo. La referencia agregada de
    # Banrep dice si una oferta va bien encaminada; esto dice cuánto se está
    # dejando encima de la mesa, que es la decisión real.
    best_bank_rate_pct: float | None = None
    best_bank_name: str | None = None
    median_bank_rate_pct: float | None = None
    issuer_market_rate_pct: float | None = None
    banks_compared: int = 0
    bank_term_labels: list[str] = field(default_factory=list)
    bank_rates_as_of: dt.date | None = None
    questions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def get_terms(db: Session, asset: Asset) -> FixedIncomeTerms | None:
    return db.scalar(
        select(FixedIncomeTerms).where(FixedIncomeTerms.asset_id == asset.id)
    )


def effective_annual_rate(
    terms: FixedIncomeTerms, indices: dict[str, rates_service.RateReading]
) -> tuple[Decimal | None, str]:
    """Tasa efectiva anual vigente y de dónde sale.

    En las indexadas la tasa de HOY no es la que regirá mañana, y eso no se
    puede esconder: se usa el último valor publicado del índice y se dice cuál
    y de cuándo. Sin índice disponible NO se inventa el spread como si fuera la
    tasa entera: se devuelve None, porque un 2,5% presentado como rentabilidad
    cuando es un diferencial sobre el IBR erraría por cinco veces.
    """
    if terms.rate_kind is RateKind.FIXED:
        return terms.annual_rate_pct, "tasa fija pactada"

    if terms.rate_kind is RateKind.UVR:
        # La UVR ajusta el CAPITAL con la inflación, así que la tasa pactada ya
        # es REAL. Sumarle la inflación aquí la contaría dos veces.
        return terms.annual_rate_pct, "tasa real sobre UVR (el capital se indexa)"

    clave = INDEX_SERIES.get(terms.rate_kind)
    lectura = indices.get(clave) if clave else None
    if lectura is None:
        return None, f"sin el valor de {terms.rate_kind.value} para componer la tasa"

    total = Decimal(str(lectura.value)) + terms.annual_rate_pct
    return total, (
        f"{lectura.label} ({lectura.value:.2f}% del {lectura.as_of.isoformat()}) "
        f"+ {terms.annual_rate_pct}%"
    )


def accrue(
    terms: FixedIncomeTerms,
    rate_pct: Decimal,
    *,
    today: dt.date | None = None,
) -> Accrual:
    """Factor de devengo desde la emisión. Ver el docstring del módulo."""
    today = today or dt.date.today()
    fin = min(today, terms.matures_on)
    transcurridos = max(0, (fin - terms.issued_on).days)
    restantes = (terms.matures_on - today).days

    tasa = rate_pct / Decimal(100)
    exponente = Decimal(transcurridos) / DAYS_IN_YEAR
    factor = (Decimal(1) + tasa) ** exponente

    if today >= terms.matures_on:
        aviso = (
            f"Venció el {terms.matures_on.isoformat()}. El valor mostrado es el "
            f"de vencimiento: si ya lo cobraste, registra la venta para que la "
            f"caja lo refleje."
        )
    else:
        aviso = (
            "Valorado a costo más devengo, que es lo que vale SI LO LLEVAS A "
            "VENCIMIENTO. No es lo que alguien pagaría hoy por él: si las tasas "
            "del mercado suben, vale menos."
        )

    return Accrual(
        factor=factor.quantize(Decimal("0.00000001")),
        effective_rate_pct=rate_pct,
        days_elapsed=transcurridos,
        days_to_maturity=restantes,
        is_matured=today >= terms.matures_on,
        basis="costo_mas_devengo",
        caveat=aviso,
    )


def assess(
    db: Session, asset: Asset, terms: FixedIncomeTerms, *, today: dt.date | None = None
) -> FixedIncomeAssessment:
    """Las cinco preguntas que deciden si un CDT o un TES vale la pena."""
    today = today or dt.date.today()
    indices = rates_service.latest(db)

    tasa, procedencia = effective_annual_rate(terms, indices)
    avisos: list[str] = []
    if tasa is None:
        avisos.append(
            f"No se pudo componer la tasa: {procedencia}. Sincroniza las tasas de "
            f"referencia o registra el instrumento con tasa fija."
        )
        tasa = terms.annual_rate_pct

    nominal = float(tasa)
    neta = (
        nominal * (1 - float(terms.withholding_pct) / 100)
        if terms.withholding_pct is not None
        else None
    )
    base_para_real = neta if neta is not None else nominal

    inflacion = indices.get("inflacion_anual")
    real = (
        rates_service.real_rate(base_para_real, inflacion.value)
        if inflacion and terms.rate_kind is not RateKind.UVR
        else (base_para_real if terms.rate_kind is RateKind.UVR else None)
    )

    # Referencia de mercado: lo que pagan instrumentos de plazo parecido.
    restantes = (terms.matures_on - today).days
    clave_ref = (
        "cdt_180d" if restantes <= 270
        else "cdt_360d" if restantes <= 540
        else "tes_cop_5y" if restantes <= 2200
        else "tes_cop_10y"
    )
    referencia = indices.get(clave_ref)

    preguntas = [
        "¿Quién te debe el dinero? En renta fija el emisor ES el riesgo: medio "
        f"punto más de un banco pequeño no compensa. Aquí el emisor es {terms.issuer}.",
        "¿Puedes salir antes? "
        + (
            "Este instrumento tiene mercado secundario, pero vender antes de "
            "tiempo puede ser con pérdida si las tasas subieron."
            if terms.has_secondary_market
            else "NO tiene mercado secundario: el dinero queda inmovilizado hasta "
            f"el {terms.matures_on.isoformat()}."
        ),
        "¿La tasa es fija o se mueve? "
        + (
            "Es fija: te protege si las tasas bajan y te perjudica si suben."
            if terms.rate_kind is RateKind.FIXED
            else f"Está indexada a {terms.rate_kind.value}: sube y baja con el "
            "índice, así que la rentabilidad de hoy no es la de mañana."
        ),
        "¿Le gana a la inflación? Lo que importa no es el número nominal sino "
        "cuánto poder adquisitivo te queda después.",
        "¿Le gana a lo que paga el mercado por un plazo parecido? Si no, estás "
        "aceptando menos por el mismo tiempo inmovilizado.",
    ]

    # Comparación contra lo que pagaron los bancos a este plazo.
    comparacion = rates_service.compare_cdt(
        db, days=max(1, restantes), issuer=terms.issuer
    )
    if comparacion is not None:
        tramos = " y ".join(f"«{x.lower()}»" for x in comparacion.term_labels)
        preguntas.append(
            f"¿Y frente a los otros bancos? En el tramo {tramos} el mejor fue "
            f"{comparacion.best_entity} con {comparacion.best_rate_pct:.2f}% y la "
            f"mediana {comparacion.median_rate_pct:.2f}%, sobre "
            f"{comparacion.offers_considered} emisiones. " + comparacion.caveat
        )
        if (
            comparacion.issuer_rate_pct is not None
            and nominal < comparacion.issuer_rate_pct - 0.25
        ):
            avisos.append(
                f"{comparacion.issuer_name} pagó {comparacion.issuer_rate_pct:.2f}% "
                f"a este plazo en el corte del {comparacion.as_of.isoformat()}, por "
                f"encima del {nominal:.2f}% que tienes registrado. Puede que la "
                f"tasa que te dieron fuera negociable."
            )

    if inflacion and inflacion.is_stale_monthly:
        avisos.append(
            f"El dato de inflación es del {inflacion.as_of.isoformat()} y ya tiene "
            f"más de 45 días: sincroniza las tasas de referencia."
        )
    if terms.withholding_pct is None:
        avisos.append(
            "No declaraste retención en la fuente: la comparación con la inflación "
            "usa la tasa BRUTA y por tanto sale mejor de lo que vas a recibir."
        )

    return FixedIncomeAssessment(
        symbol=asset.symbol,
        issuer=terms.issuer,
        currency=asset.currency,
        rate_kind=terms.rate_kind.value,
        nominal_rate_pct=round(nominal, 4),
        net_rate_pct=round(neta, 4) if neta is not None else None,
        real_rate_pct=round(real, 4) if real is not None else None,
        inflation_pct=inflacion.value if inflacion else None,
        inflation_as_of=inflacion.as_of if inflacion else None,
        market_reference_pct=referencia.value if referencia else None,
        market_reference_label=referencia.label if referencia else None,
        market_reference_as_of=referencia.as_of if referencia else None,
        beats_inflation=None if real is None else real > 0,
        # BRUTA contra BRUTA. La referencia de Banrep es la tasa pactada en el
        # mercado, antes de retención: compararla con la tasa NETA de este
        # instrumento diría que pierde contra el mercado por una retención que
        # los demás también pagan. Contra la inflación sí manda la neta,
        # porque ahí lo que se compara es poder adquisitivo.
        beats_market=(None if referencia is None else nominal > referencia.value),
        days_to_maturity=restantes,
        has_secondary_market=terms.has_secondary_market,
        best_bank_rate_pct=comparacion.best_rate_pct if comparacion else None,
        best_bank_name=comparacion.best_entity if comparacion else None,
        median_bank_rate_pct=comparacion.median_rate_pct if comparacion else None,
        issuer_market_rate_pct=comparacion.issuer_rate_pct if comparacion else None,
        banks_compared=comparacion.offers_considered if comparacion else 0,
        bank_term_labels=list(comparacion.term_labels) if comparacion else [],
        bank_rates_as_of=comparacion.as_of if comparacion else None,
        questions=preguntas,
        warnings=avisos,
    )


def all_terms(db: Session) -> dict[int, FixedIncomeTerms]:
    """Condiciones de todos los instrumentos, por asset_id."""
    return {
        fila.asset_id: fila
        for fila in db.scalars(select(FixedIncomeTerms)).all()
    }


def accrual_quotes(
    db: Session, *, today: dt.date | None = None
) -> list[tuple[Asset, Accrual]]:
    """Devengo de cada instrumento vivo, listo para publicarse como cotización.

    Es puro cálculo: no sale a la red. Por eso puede correr en el mismo sitio
    que el refresco de cotizaciones sin ninguna de sus precauciones.
    """
    today = today or dt.date.today()
    indices = rates_service.latest(db)
    filas = db.scalars(
        select(Asset).where(Asset.asset_type == AssetType.FIXED_INCOME)
    ).all()

    resultado: list[tuple[Asset, Accrual]] = []
    for asset in filas:
        terms = get_terms(db, asset)
        if terms is None:
            continue
        tasa, _ = effective_annual_rate(terms, indices)
        if tasa is None:
            continue
        resultado.append((asset, accrue(terms, tasa, today=today)))
    return resultado


def publish_accruals(db: Session, *, today: dt.date | None = None) -> int:
    """Escribe el devengo de cada instrumento como una cotización más.

    ES LO QUE EVITA UN CASO ESPECIAL EN TODO EL SISTEMA. Con la cotización
    publicada, el valor de la posición, la curva de rendimiento, el reparto por
    clase y la atribución de divisa funcionan sin saber que esto no cotiza.

    `source="devengo"` es la declaración: cualquiera que mire la fila sabe que
    ese precio no vino de un mercado. No se toca `is_stale`, que queda en False
    a propósito: el devengo de hoy ES el valor de hoy bajo su propio supuesto,
    y marcarlo rancio diría que el dato está viejo cuando lo que pasa es que la
    base de valoración es otra.
    """
    from app.models import AssetQuote

    escritas = 0
    ahora = dt.datetime.now(dt.UTC)
    for asset, devengo in accrual_quotes(db, today=today):
        quote = db.get(AssetQuote, asset.id)
        if quote is None:
            quote = AssetQuote(
                asset_id=asset.id, price=1.0, currency=asset.currency
            )
            db.add(quote)
        quote.price = float(devengo.factor)
        quote.currency = asset.currency
        quote.quote_time = ahora
        quote.fetched_at = ahora
        quote.is_stale = False
        quote.source = "devengo"
        escritas += 1

    if escritas:
        db.commit()
    return escritas
