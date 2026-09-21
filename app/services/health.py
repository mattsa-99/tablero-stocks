"""Salud financiera y calendario, leídos del payload íntegro de Yahoo (`raw`).

POR QUÉ EXISTE
==============
`FundamentalSnapshot.raw` guarda ~170 campos por empresa y el motor solo usa
una docena. Lo que se queda fuera es justo lo que un principiante necesita para
no comprar una empresa que quema caja o que reporta resultados pasado mañana:
flujo de caja libre, deuda neta, EBITDA, margen operativo, current ratio y las
fechas de resultados y de dividendo.

TRES REGLAS QUE NO SE PUEDEN RELAJAR
====================================
1. LA MONEDA. Las cifras de estado financiero vienen en la moneda en que la
   empresa REPORTA (`financialCurrency`), que no siempre es aquella en que
   cotiza el ADR: PBR cotiza en USD y reporta en BRL; TM, en JPY; CIB, en COP.
   Por eso solo se calculan COCIENTES ENTRE CIFRAS DEL MISMO REPORTE (deuda
   neta / EBITDA, caja libre / ventas): son inmunes a la moneda. Cualquier
   cociente que mezcle una cifra del reporte con el precio o la capitalización
   (rendimiento de caja libre) solo se calcula si las dos monedas coinciden y
   la capitalización es coherente con precio × acciones. El `enterpriseValue`
   NO se usa nunca: en los ADR es incoherente (PBR: 450.000 M frente a una
   capitalización de 140.000 M USD y una deuda neta de 313.000 M BRL).
2. LOS BANCOS. La deuda y la caja son la materia prima de un banco, no una
   forma de financiarse: la caja operativa de JPM es -162.000 M y su EBITDA
   viene vacío. En bancos y aseguradoras estas métricas se declaran «no
   aplican» en vez de calcularlas y presentar un número sin sentido.
3. LO AUSENTE SE QUEDA AUSENTE. Un campo que Yahoo no trae es `None`, nunca 0:
   un 0 sería «no tiene deuda», que es una afirmación.

FECHAS. Solo se devuelven las FUTURAS. El snapshot puede tener días de
antigüedad y `earningsTimestamp` suele traer la última fecha de resultados, ya
pasada (AAPL: 2026-07-30 con el snapshot del 2026-09-19); presentarla como
«próximos resultados» sería falso, así que una fecha pasada equivale a «no
consta la próxima».
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any

# Industrias donde deuda neta / EBITDA, caja libre y margen operativo NO
# significan lo mismo que en una empresa industrial. Se compara por prefijo:
# Yahoo escribe «Banks - Regional», «Insurance - Diversified», etc.
FINANCIAL_INDUSTRY_PREFIXES: tuple[str, ...] = (
    "Banks",
    "Insurance",
    "Capital Markets",
    "Mortgage",
)

# Industrias con un brazo financiero cautivo: su deuda TOTAL incluye la de los
# préstamos que conceden a sus clientes (Toyota Financial Services, Caterpillar
# Financial...), así que deuda neta / EBITDA exagera el apalancamiento del
# negocio industrial. No se oculta: se avisa, y la bandera baja de rojo a
# amarillo.
CAPTIVE_FINANCE_INDUSTRIES: tuple[str, ...] = (
    "Auto Manufacturers",
    "Farm & Heavy Construction Machinery",
    "Rental & Leasing",
    "Conglomerates",
)

# Sectores con deuda estructuralmente alta y estable: contratos regulados,
# alquileres o concesiones. Los umbrales de deuda neta / EBITDA se relajan.
STRUCTURALLY_LEVERAGED_SECTORS: frozenset[str] = frozenset(
    {"Utilities", "Real Estate", "Communication Services"}
)

# Coherencia de la capitalización: `marketCap` debe parecerse a precio ×
# acciones. Las clases duales (BRK-B, GOOGL) y los ADR con ratio distinto de 1
# rompen la igualdad y con ella cualquier cociente que use la capitalización.
MARKET_CAP_TOLERANCE: tuple[float, float] = (0.65, 1.5)

# Un precio objetivo que dista más que esto del precio no es una opinión, es un
# dato roto (moneda distinta, escala de ADR).
PLAUSIBLE_TARGET_UPSIDE: tuple[float, float] = (-0.9, 5.0)


@dataclass(frozen=True)
class CalendarInfo:
    """Fechas próximas. `None` significa «no consta una fecha futura»."""

    next_earnings: dt.date | None = None
    earnings_is_estimate: bool = False
    days_to_earnings: int | None = None
    ex_dividend: dt.date | None = None
    days_to_ex_dividend: int | None = None


@dataclass(frozen=True)
class AnalystView:
    """El consenso de analistas. Es OPINIÓN, no dato, y así se presenta."""

    target_mean: float | None = None
    analysts: int | None = None
    recommendation: str | None = None
    upside_pct: float | None = None


@dataclass
class HealthSnapshot:
    has_data: bool = False
    # False en bancos y aseguradoras: las métricas de deuda, caja y margen no
    # significan lo mismo. `not_applicable_reason` dice por qué.
    applies: bool = True
    not_applicable_reason: str | None = None
    reporting_currency: str | None = None
    quote_currency: str | None = None
    currency_mismatch: bool = False
    # Cocientes. Fracciones (0,12 = 12%) o múltiplos ("x"), nunca mezclados.
    net_debt_to_ebitda: float | None = None
    fcf_margin: float | None = None
    fcf_yield: float | None = None
    operating_margin: float | None = None
    current_ratio: float | None = None
    payout_ratio: float | None = None
    # Cifras absolutas, EN LA MONEDA DE REPORTE (`reporting_currency`).
    free_cash_flow: float | None = None
    operating_cash_flow: float | None = None
    total_debt: float | None = None
    total_cash: float | None = None
    ebitda: float | None = None
    calendar: CalendarInfo = field(default_factory=CalendarInfo)
    analyst: AnalystView = field(default_factory=AnalystView)
    # Por qué una métrica falta o debe leerse con cuidado.
    caveats: list[str] = field(default_factory=list)
    unavailable: dict[str, str] = field(default_factory=dict)
    is_captive_finance: bool = False


def _num(raw: dict[str, Any], key: str) -> float | None:
    """Un número finito, o None. Los bool NO son números (isinstance(True, int))."""
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _date_from_timestamp(value: float | None) -> dt.date | None:
    if value is None or value <= 0:
        return None
    try:
        return dt.datetime.fromtimestamp(value, dt.UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


def _is_financial(industry: str | None) -> bool:
    return bool(industry) and industry.startswith(FINANCIAL_INDUSTRY_PREFIXES)


def _is_captive_finance(industry: str | None) -> bool:
    return bool(industry) and any(name in industry for name in CAPTIVE_FINANCE_INDUSTRIES)


def _market_cap_is_coherent(
    market_cap: float | None, price: float | None, shares: float | None
) -> bool | None:
    """True/False, o None si no se puede comprobar (falta algún dato)."""
    if not market_cap or not price or not shares or price <= 0 or shares <= 0:
        return None
    ratio = market_cap / (price * shares)
    low, high = MARKET_CAP_TOLERANCE
    return low <= ratio <= high


def _calendar(raw: dict[str, Any], today: dt.date) -> CalendarInfo:
    earnings = _date_from_timestamp(_num(raw, "earningsTimestamp"))
    ex_dividend = _date_from_timestamp(_num(raw, "exDividendDate"))

    if earnings is not None and earnings < today:
        earnings = None
    if ex_dividend is not None and ex_dividend < today:
        ex_dividend = None

    return CalendarInfo(
        next_earnings=earnings,
        earnings_is_estimate=bool(raw.get("isEarningsDateEstimate")) and earnings is not None,
        days_to_earnings=(earnings - today).days if earnings else None,
        ex_dividend=ex_dividend,
        days_to_ex_dividend=(ex_dividend - today).days if ex_dividend else None,
    )


def _analyst(raw: dict[str, Any], price: float | None) -> AnalystView:
    target = _num(raw, "targetMeanPrice")
    count = _num(raw, "numberOfAnalystOpinions")
    key = raw.get("recommendationKey")
    recommendation = key if isinstance(key, str) and key and key != "none" else None

    upside = None
    if target and target > 0 and price and price > 0:
        candidate = target / price - 1.0
        low, high = PLAUSIBLE_TARGET_UPSIDE
        if low <= candidate <= high:
            upside = candidate
        else:
            target = None          # incoherente: no se enseña ni el objetivo

    return AnalystView(
        target_mean=target,
        analysts=int(count) if count is not None else None,
        recommendation=recommendation,
        upside_pct=upside,
    )


def extract_health(
    raw: dict[str, Any] | None,
    *,
    quote_currency: str | None,
    sector: str | None,
    industry: str | None,
    price: float | None,
    today: dt.date | None = None,
) -> HealthSnapshot:
    """Extrae salud financiera, calendario y consenso del payload de Yahoo."""
    today = today or dt.date.today()
    if not raw:
        return HealthSnapshot(
            has_data=False,
            quote_currency=quote_currency,
            caveats=["Sin datos crudos del proveedor para esta empresa."],
        )

    reporting = raw.get("financialCurrency")
    reporting = reporting.upper() if isinstance(reporting, str) and reporting else None
    quote = quote_currency.upper() if quote_currency else None
    mismatch = bool(reporting and quote and reporting != quote)

    snapshot = HealthSnapshot(
        has_data=True,
        reporting_currency=reporting,
        quote_currency=quote,
        currency_mismatch=mismatch,
        calendar=_calendar(raw, today),
        analyst=_analyst(raw, price),
        is_captive_finance=_is_captive_finance(industry),
    )

    if mismatch:
        snapshot.caveats.append(
            f"Cotiza en {quote} pero reporta en {reporting}: las cifras absolutas "
            f"están en {reporting}. Solo se usan cocientes entre cifras del mismo "
            f"reporte, y el rendimiento de caja libre no se calcula."
        )
    if snapshot.is_captive_finance:
        snapshot.caveats.append(
            "Su deuda total incluye la de su brazo financiero (préstamos a "
            "clientes): la deuda neta sobre EBITDA exagera el apalancamiento del "
            "negocio industrial."
        )
    if sector == "Real Estate":
        snapshot.caveats.append(
            "Es una inmobiliaria: su beneficio contable no refleja su caja "
            "(depreciación de inmuebles), así que el reparto de dividendo sobre "
            "beneficio no es fiable."
        )

    # Los ratios de mercado que no dependen de las cifras de estado financiero
    # se calculan siempre, también en bancos.
    snapshot.payout_ratio = None if sector == "Real Estate" else _num(raw, "payoutRatio")

    if _is_financial(industry):
        snapshot.applies = False
        snapshot.not_applicable_reason = (
            "En bancos y aseguradoras la deuda y la caja son la materia prima del "
            "negocio, no una forma de financiarse: deuda neta sobre EBITDA, caja "
            "libre y margen operativo no significan lo mismo que en una empresa "
            "industrial. Para un banco mira ROE, solvencia, cartera vencida y "
            "eficiencia en sus propios reportes."
        )
        return snapshot

    debt = _num(raw, "totalDebt")
    cash = _num(raw, "totalCash")
    ebitda = _num(raw, "ebitda")
    fcf = _num(raw, "freeCashflow")
    ocf = _num(raw, "operatingCashflow")
    revenue = _num(raw, "totalRevenue")

    snapshot.total_debt, snapshot.total_cash, snapshot.ebitda = debt, cash, ebitda
    snapshot.free_cash_flow, snapshot.operating_cash_flow = fcf, ocf
    snapshot.operating_margin = _num(raw, "operatingMargins")
    snapshot.current_ratio = _num(raw, "currentRatio")

    # --- deuda neta / EBITDA: cociente entre cifras del MISMO reporte ---
    if debt is None or cash is None or ebitda is None:
        snapshot.unavailable["net_debt_to_ebitda"] = "Yahoo no trae deuda, caja o EBITDA"
    elif ebitda <= 0:
        snapshot.unavailable["net_debt_to_ebitda"] = (
            "EBITDA no positivo: el cociente no tiene sentido"
        )
    else:
        snapshot.net_debt_to_ebitda = (debt - cash) / ebitda

    # --- caja libre sobre ventas: mismo reporte ---
    if fcf is None or revenue is None or revenue <= 0:
        snapshot.unavailable["fcf_margin"] = "Yahoo no trae caja libre o ventas"
    else:
        snapshot.fcf_margin = fcf / revenue

    # --- rendimiento de caja libre: mezcla reporte con capitalización ---
    market_cap = _num(raw, "marketCap")
    if fcf is None or market_cap is None or market_cap <= 0:
        snapshot.unavailable["fcf_yield"] = "Falta la caja libre o la capitalización"
    elif mismatch:
        snapshot.unavailable["fcf_yield"] = (
            f"La caja libre está en {reporting} y la capitalización en {quote}"
        )
    else:
        coherent = _market_cap_is_coherent(market_cap, price, _num(raw, "sharesOutstanding"))
        if coherent is False:
            snapshot.unavailable["fcf_yield"] = (
                "La capitalización no cuadra con precio × acciones (clases duales "
                "o ADR con ratio distinto de 1)"
            )
        else:
            snapshot.fcf_yield = fcf / market_cap

    return snapshot
