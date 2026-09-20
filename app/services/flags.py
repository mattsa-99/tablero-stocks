"""Banderas de una compra: lo que un principiante suele pasar por alto.

FILOSOFÍA
=========
Cada bandera responde a UNA pregunta concreta con UNA cifra, y dice de dónde
sale. Hay cuatro niveles:

    red     un problema objetivo: pérdidas, historial parado, deuda extrema
    yellow  algo que hay que entender antes de comprar
    green   un punto a favor (nunca solo: no compensa una roja)
    info    contexto que cambia cómo se lee el resto

El veredicto resume, pero NUNCA dice «compra». Sin banderas rojas no significa
buena inversión: significa que este filtro no encontró problemas. Esa
diferencia es lo que un semáforo verde tiende a borrar en quien empieza.

LOS UMBRALES son convenciones razonadas, no resultado de un backtest, igual que
los de `grading.py`. Están arriba como constantes con su porqué para poder
discutirlos.

SEMÁFORO ASIMÉTRICO. Es más fácil ponerse amarillo que verde, a propósito: para
quien empieza, un falso positivo cuesta una revisión y un falso negativo cuesta
dinero.
"""

from __future__ import annotations

from app.schemas.ficha import Flag, Verdict
from app.schemas.opportunity import OpportunityRead
from app.services.health import (
    STRUCTURALLY_LEVERAGED_SECTORS,
    CalendarInfo,
    HealthSnapshot,
)

# --- Umbrales ---------------------------------------------------------------

# Momentum 12-1 por encima del cual «ya subió mucho». No es una señal de venta:
# es que comprar tras subir la mitad exige una razón más fuerte que «sube».
RAN_UP_MOMENTUM = 0.50

# Volatilidad anualizada y caída máxima. Alineados con las bandas de
# `grading.py` («alta» desde 35%, «muy alta» desde 50%).
HIGH_VOLATILITY = 0.35
EXTREME_VOLATILITY = 0.50
DEEP_DRAWDOWN = 0.35
EXTREME_DRAWDOWN = 0.50

# Deuda neta / EBITDA. Por encima de 3x la empresa tarda más de tres años de
# EBITDA en pagar su deuda; por encima de 4,5x cualquier caída de ventas la
# aprieta. Los sectores de deuda estructural (regulados, alquileres,
# concesiones) toleran más.
LEVERAGE_WARN = 3.0
LEVERAGE_RED = 4.5
LEVERAGE_WARN_STRUCTURAL = 5.0
LEVERAGE_RED_STRUCTURAL = 7.0

# Ratio de valoración frente a su referencia (sector o mercado). Alineado con
# las bandas de `grading.py`: «cara» desde 1,3 y «muy cara» desde 1,8.
EXPENSIVE_RATIO = 1.3
VERY_EXPENSIVE_RATIO = 1.8
CHEAP_RATIO = 0.7

# Días para considerar «inminente» un reporte de resultados o un ex-dividendo.
EARNINGS_SOON_DAYS = 14
EX_DIVIDEND_SOON_DAYS = 14

# Fracción del ranking que se considera «los primeros».
TOP_RANK_FRACTION = 0.10

# Sectores cuyas ganancias oscilan con el ciclo: un P/E bajo puede ser el pico.
CYCLICAL_SECTORS: frozenset[str] = frozenset(
    {"Energy", "Basic Materials", "Consumer Cyclical", "Industrials"}
)

# Sectores donde un current ratio < 1 es normal (cobran antes de pagar o tienen
# deuda de largo plazo regulada).
LOW_CURRENT_RATIO_NORMAL_SECTORS: frozenset[str] = frozenset({"Utilities"})

_REGION_NAME = {
    "COL": "Colombia",
    "LATAM": "Latinoamérica",
    "EU": "Europa",
    "ASIA": "Asia",
}


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def build_flags(
    opportunity: OpportunityRead,
    health: HealthSnapshot,
    *,
    universe_size: int | None = None,
    portfolio_position_count: int = 0,
    concentration_threshold: float = 0.30,
) -> list[Flag]:
    """Todas las banderas de una empresa, en el orden en que se leen."""
    flags: list[Flag] = []
    sector = opportunity.sector

    flags += _quality_flags(opportunity, universe_size)
    flags += _valuation_flags(opportunity)
    flags += _price_flags(opportunity)
    flags += _balance_sheet_flags(opportunity, health)
    flags += _calendar_flags(health.calendar)
    flags += _data_flags(opportunity, health)
    flags += _portfolio_flags(
        opportunity, portfolio_position_count, concentration_threshold
    )

    # Contexto de sector: solo si cambia cómo se lee el resto.
    if sector in CYCLICAL_SECTORS:
        cheap = any(f.code == "valuation_cheap" for f in flags)
        flags.append(Flag(
            code="cyclical_sector", level="yellow" if cheap else "info",
            title="Sector cíclico",
            detail=(
                f"{sector} vive de ciclos: sus ganancias suben y bajan con la "
                "economía y con los precios de lo que vende. Un P/E bajo puede "
                "ser el PICO de ganancias, justo antes de que caigan. Mira las "
                "ganancias de varios años, no solo las del último."
            ),
            evidence={"sector": sector},
        ))

    flags.append(Flag(
        code="verify_ticker", level="info",
        title="Comprueba el instrumento con tu broker",
        detail=(
            f"«{opportunity.symbol}» es el ticker de Yahoo. Antes de comprar, "
            "confirma que tu broker ofrece exactamente ese instrumento (y no "
            "otra clase de acción, otro listado u otro ADR)."
        ),
    ))

    return flags


# ---------------------------------------------------------------------------
# Calidad
# ---------------------------------------------------------------------------


def _quality_flags(op: OpportunityRead, universe_size: int | None) -> list[Flag]:
    a = op.assessment
    evidence = {"grade": a.grade, "points": a.points, "max_points": a.max_points}
    flags: list[Flag] = []

    if a.grade in ("D", "E"):
        flags.append(Flag(
            code="grade_bad", level="red",
            title=f"Calificación absoluta «{a.label}»",
            detail=(
                f"Suma {a.points} de {a.max_points} puntos. Puede aparecer arriba "
                "en el ranking solo por ser la menos mala de un grupo: el ranking "
                "es relativo, esta calificación no."
            ),
            evidence=evidence,
        ))
    elif a.grade == "C":
        flags.append(Flag(
            code="grade_mediocre", level="yellow",
            title="Calificación absoluta «Normal»",
            detail=(
                f"{a.points} de {a.max_points} puntos: cumple lo convencional pero "
                "no destaca. No hay una razón objetiva para preferirla a otras."
            ),
            evidence=evidence,
        ))
    elif a.grade == "SIN_CALIFICAR":
        flags.append(Flag(
            code="grade_unrated", level="yellow",
            title="Sin calificación",
            detail="Faltan datos: hacen falta al menos 2 de las 4 señales.",
            evidence=evidence,
        ))
    else:
        flags.append(Flag(
            code="grade_good", level="green",
            title=f"Calificación absoluta «{a.label}»",
            detail=(
                f"{a.points} de {a.max_points} puntos en valoración, tendencia, "
                "riesgo y calidad."
            ),
            evidence=evidence,
        ))

    if (
        universe_size
        and op.rank <= max(1, int(universe_size * TOP_RANK_FRACTION))
        and a.grade in ("C", "D", "E", "SIN_CALIFICAR")
    ):
        flags.append(Flag(
            code="rank_vs_grade", level="yellow",
            title="Lidera el ranking sin ser buena",
            detail=(
                f"Ocupa el puesto {op.rank} de {universe_size}, pero su "
                f"calificación absoluta es «{a.label}». El ranking dice quién es "
                "el mejor del grupo; no dice si ese mejor es bueno."
            ),
            evidence={"rank": op.rank, "universe_size": universe_size, "grade": a.grade},
        ))
    return flags


# ---------------------------------------------------------------------------
# Valoración
# ---------------------------------------------------------------------------


def _valuation_flags(op: OpportunityRead) -> list[Flag]:
    signal = next((s for s in op.assessment.signals if s.name == "valuation"), None)
    if signal is None or signal.points is None:
        return []

    if "no tiene beneficios positivos" in signal.detail:
        return [Flag(
            code="no_earnings", level="red",
            title="La empresa pierde dinero",
            detail=(
                "No tiene beneficios positivos: su P/E no existe. Un múltiplo "
                "negativo NO es «barato», es una empresa en pérdidas."
            ),
            evidence={"trailing_pe": signal.inputs.get("trailing_pe")},
        )]

    ratio = signal.inputs.get("pe_vs_reference")
    reference = signal.inputs.get("reference_pe")
    if ratio is None:
        return []

    basis = (
        f"su sector ({op.value_reference})" if op.value_basis == "sector" else "el mercado"
    )
    evidence = {"pe_vs_reference": ratio, "reference_pe": reference, "basis": basis}

    if ratio > VERY_EXPENSIVE_RATIO:
        return [Flag(
            code="valuation_very_expensive", level="red",
            title="Muy cara frente a " + basis,
            detail=(
                f"Su P/E es {ratio:.1f} veces el de {basis}. El precio ya "
                "descuenta un crecimiento muy alto: si no llega, cae."
            ),
            evidence=evidence,
        )]
    if ratio > EXPENSIVE_RATIO:
        return [Flag(
            code="valuation_expensive", level="yellow",
            title="Cara frente a " + basis,
            detail=(
                f"Su P/E es {ratio:.1f} veces el de {basis}. Puede estar "
                "justificado por un negocio mejor; entiende por qué antes de pagarlo."
            ),
            evidence=evidence,
        )]
    if ratio < CHEAP_RATIO:
        return [Flag(
            code="valuation_cheap", level="green",
            title="Barata frente a " + basis,
            detail=(
                f"Su P/E es el {_pct(ratio)} del de {basis}. Barata no es lo mismo "
                "que buena: descuenta riesgos que aún no has identificado, o "
                "ganancias en su punto más alto."
            ),
            evidence=evidence,
        )]
    return []


# ---------------------------------------------------------------------------
# Precio y riesgo
# ---------------------------------------------------------------------------


def _price_flags(op: OpportunityRead) -> list[Flag]:
    flags: list[Flag] = []
    momentum = op.momentum.inputs.get("momentum_12_1")
    trend = op.momentum.inputs.get("sma50_over_sma200_minus_1")
    vol = op.risk.inputs.get("annualized_volatility")
    drawdown = op.risk.inputs.get("max_drawdown")

    if momentum is not None and momentum > RAN_UP_MOMENTUM:
        flags.append(Flag(
            code="already_ran_up", level="yellow",
            title=f"Ya subió {_pct(momentum)} en 12 meses",
            detail=(
                "No es una señal de venta, pero gran parte de la buena noticia "
                "ya está en el precio. Comprar después de una subida así exige "
                "una razón distinta de «viene subiendo»."
            ),
            evidence={"momentum_12_1": round(momentum, 3)},
        ))
    elif momentum is not None and trend is not None and momentum < -0.20 and trend < -0.05:
        flags.append(Flag(
            code="falling", level="yellow",
            title="Tendencia bajista sostenida",
            detail=(
                f"Cae {_pct(abs(momentum))} en 12 meses y su media de 50 días está "
                "por debajo de la de 200. Comprar algo que cae es apostar a que "
                "el mercado se equivoca: necesitas saber por qué."
            ),
            evidence={"momentum_12_1": round(momentum, 3), "sma_trend": round(trend, 3)},
        ))

    if vol is not None and vol >= EXTREME_VOLATILITY:
        flags.append(Flag(
            code="extreme_volatility", level="red",
            title=f"Volatilidad muy alta ({_pct(vol)})",
            detail=(
                "Sus oscilaciones anuales típicas superan el 50%. Es un activo "
                "para quien aguanta ver caer la mitad."
            ),
            evidence={"annualized_volatility": round(vol, 3)},
        ))
    elif vol is not None and vol >= HIGH_VOLATILITY:
        flags.append(Flag(
            code="high_volatility", level="yellow",
            title=f"Volatilidad alta ({_pct(vol)})",
            detail=(
                "Un ETF amplio ronda el 15%. Con esta volatilidad son normales "
                "los sustos de dos dígitos en pocas semanas."
            ),
            evidence={"annualized_volatility": round(vol, 3)},
        ))

    if drawdown is not None and drawdown >= EXTREME_DRAWDOWN:
        flags.append(Flag(
            code="extreme_drawdown", level="red",
            title=f"Llegó a caer {_pct(drawdown)} en el último año",
            detail=(
                "Quien compró en el máximo vio su posición a la mitad. Piensa si "
                "lo habrías aguantado sin vender."
            ),
            evidence={"max_drawdown": round(drawdown, 3)},
        ))
    elif drawdown is not None and drawdown >= DEEP_DRAWDOWN:
        flags.append(Flag(
            code="deep_drawdown", level="yellow",
            title=f"Llegó a caer {_pct(drawdown)} en el último año",
            detail=(
                "Es la peor caída desde un máximo en 12 meses: mide lo que pudo "
                "doler comprar en el peor momento."
            ),
            evidence={"max_drawdown": round(drawdown, 3)},
        ))
    return flags


# ---------------------------------------------------------------------------
# Balance y caja (payload crudo)
# ---------------------------------------------------------------------------


def _leverage_thresholds(sector: str | None) -> tuple[float, float]:
    if sector in STRUCTURALLY_LEVERAGED_SECTORS:
        return LEVERAGE_WARN_STRUCTURAL, LEVERAGE_RED_STRUCTURAL
    return LEVERAGE_WARN, LEVERAGE_RED


def _balance_sheet_flags(op: OpportunityRead, health: HealthSnapshot) -> list[Flag]:
    if not health.has_data:
        return []
    if not health.applies:
        return [Flag(
            code="financial_metrics_na", level="info",
            title="Deuda, caja y márgenes no aplican a un banco o aseguradora",
            detail=health.not_applicable_reason or "",
        )]

    flags: list[Flag] = []
    warn, red = _leverage_thresholds(op.sector)
    nd = health.net_debt_to_ebitda

    if health.ebitda is not None and health.ebitda <= 0 and (
        (health.total_debt or 0) > (health.total_cash or 0)
    ):
        flags.append(Flag(
            code="negative_ebitda", level="red",
            title="EBITDA no positivo con deuda neta",
            detail=(
                "Su negocio no genera ganancia operativa y aun así debe más de "
                "lo que tiene en caja."
            ),
            evidence={"ebitda": health.ebitda},
        ))
    elif nd is not None:
        if nd < 0:
            flags.append(Flag(
                code="net_cash", level="green",
                title="Caja neta: tiene más caja que deuda",
                detail="Puede pagar toda su deuda con lo que tiene en caja.",
                evidence={"net_debt_to_ebitda": round(nd, 2)},
            ))
        elif nd >= red:
            level = "yellow" if health.is_captive_finance else "red"
            flags.append(Flag(
                code="leverage_high", level=level,
                title=f"Deuda neta de {nd:.1f} veces su EBITDA",
                detail=(
                    f"Tardaría {nd:.1f} años de EBITDA en pagar su deuda neta. "
                    + (
                        "Ojo: incluye la deuda de su brazo financiero, así que "
                        "exagera el riesgo del negocio industrial."
                        if health.is_captive_finance
                        else "Cualquier caída de ventas la aprieta."
                    )
                ),
                evidence={"net_debt_to_ebitda": round(nd, 2), "red_threshold": red},
            ))
        elif nd >= warn:
            flags.append(Flag(
                code="leverage_elevated", level="yellow",
                title=f"Deuda neta de {nd:.1f} veces su EBITDA",
                detail=(
                    f"Por encima de {warn:.0f}x conviene entender de dónde sale la "
                    "deuda y con qué caja la pagará."
                ),
                evidence={"net_debt_to_ebitda": round(nd, 2), "warn_threshold": warn},
            ))

    fcf, ocf = health.free_cash_flow, health.operating_cash_flow
    if ocf is not None and ocf <= 0:
        flags.append(Flag(
            code="no_operating_cash", level="red",
            title="El negocio no genera caja",
            detail=(
                "Su caja operativa es negativa o cero: paga sus gastos con deuda "
                "o con capital nuevo."
            ),
            evidence={"operating_cash_flow": ocf},
        ))
    elif fcf is not None and fcf < 0:
        flags.append(Flag(
            code="fcf_negative", level="yellow",
            title="Gasta más en inversión de lo que genera",
            detail=(
                "Su caja libre es negativa: lo que genera no alcanza para su "
                "inversión (capex). Es normal en empresas en expansión o reguladas, "
                "pero se financia con deuda o con nuevos accionistas."
            ),
            evidence={"free_cash_flow": fcf, "fcf_margin": _round(health.fcf_margin)},
        ))
    elif health.fcf_margin is not None and health.fcf_margin >= 0.05:
        flags.append(Flag(
            code="fcf_positive", level="green",
            title=f"Genera caja libre ({_pct(health.fcf_margin)} de sus ventas)",
            detail="Después de invertir en su negocio, le sobra caja.",
            evidence={"fcf_margin": _round(health.fcf_margin)},
        ))

    cr = health.current_ratio
    if cr is not None and cr < 1.0 and op.sector not in LOW_CURRENT_RATIO_NORMAL_SECTORS:
        flags.append(Flag(
            code="current_ratio_low", level="yellow",
            title=f"Liquidez ajustada (current ratio {cr:.2f})",
            detail=(
                "Sus deudas de corto plazo superan sus activos de corto plazo. En "
                "algunos sectores es normal; en otros es una señal de tensión."
            ),
            evidence={"current_ratio": round(cr, 2)},
        ))

    payout = health.payout_ratio
    if payout is not None and payout > 1.0:
        flags.append(Flag(
            code="payout_high", level="yellow",
            title=f"Paga en dividendos el {_pct(payout)} de lo que gana",
            detail="Reparte más de lo que gana: solo se sostiene con caja acumulada o deuda.",
            evidence={"payout_ratio": round(payout, 2)},
        ))
    return flags


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


# ---------------------------------------------------------------------------
# Calendario
# ---------------------------------------------------------------------------


def _calendar_flags(calendar: CalendarInfo) -> list[Flag]:
    flags: list[Flag] = []
    days = calendar.days_to_earnings
    if days is not None and days <= EARNINGS_SOON_DAYS:
        estimate = " (fecha estimada)" if calendar.earnings_is_estimate else ""
        flags.append(Flag(
            code="earnings_soon", level="yellow",
            title=f"Reporta resultados en {days} días{estimate}",
            detail=(
                "Comprar justo antes de un reporte es apostar al resultado: el "
                "precio puede moverse con fuerza en cualquier dirección. Muchos "
                "prefieren esperar al reporte."
            ),
            evidence={"date": calendar.next_earnings.isoformat(), "days": days},
        ))

    days = calendar.days_to_ex_dividend
    if days is not None and days <= EX_DIVIDEND_SOON_DAYS:
        flags.append(Flag(
            code="ex_dividend_soon", level="info",
            title=f"Fecha ex-dividendo en {days} días",
            detail=(
                "Quien compre después de esa fecha no recibe el próximo "
                "dividendo. Y el día ex-dividendo el precio baja aproximadamente "
                "lo que se paga: comprar antes solo para cobrarlo no es ganancia."
            ),
            evidence={"date": calendar.ex_dividend.isoformat(), "days": days},
        ))
    return flags


# ---------------------------------------------------------------------------
# Datos
# ---------------------------------------------------------------------------


def _data_flags(op: OpportunityRead, health: HealthSnapshot) -> list[Flag]:
    flags: list[Flag] = []

    if any("Histórico detenido" in note for note in op.notes):
        flags.append(Flag(
            code="stale_prices", level="red",
            title="Histórico de precios detenido",
            detail=(
                "La última barra es vieja: no se puntúan tendencia ni riesgo. "
                "Cualquier cifra de precio de esta ficha puede no ser la de hoy."
            ),
        ))
    if any("Sin datos fundamentales" in note for note in op.notes):
        flags.append(Flag(
            code="no_fundamentals", level="yellow",
            title="Sin datos fundamentales",
            detail="No hay estados financieros de esta empresa en el Tablero.",
        ))
    if op.data_completeness < 0.9 and not any(f.code == "stale_prices" for f in flags):
        flags.append(Flag(
            code="low_completeness", level="yellow",
            title="Datos incompletos",
            detail=(
                f"Solo el {_pct(op.data_completeness)} de los factores del score "
                "tiene dato real; el resto se imputó como neutro."
            ),
            evidence={"data_completeness": op.data_completeness},
        ))

    if health.currency_mismatch:
        flags.append(Flag(
            code="reporting_currency_differs", level="info",
            title=f"Cotiza en {health.quote_currency} pero reporta en {health.reporting_currency}",
            detail=(
                "Yahoo no convierte la divisa en P/B, P/S y EV/EBITDA, y salen "
                "números plausibles y falsos: el Tablero los descarta. El P/E sí "
                "es fiable."
            ),
            evidence={
                "quote_currency": health.quote_currency,
                "reporting_currency": health.reporting_currency,
            },
        ))

    region_name = _REGION_NAME.get(op.market_region)
    if region_name and op.currency == "USD":
        flags.append(Flag(
            code="foreign_business_fx", level="info",
            title=f"Negocio en {region_name}, precio en dólares",
            detail=(
                f"Su ADR cotiza en USD pero su negocio y su moneda son de "
                f"{region_name}: el precio incorpora el movimiento de esa moneda "
                "frente al dólar además de la marcha de la empresa."
            ),
            evidence={"region": op.market_region},
        ))
    return flags


# ---------------------------------------------------------------------------
# Cartera
# ---------------------------------------------------------------------------


def _portfolio_flags(
    op: OpportunityRead, position_count: int, threshold: float
) -> list[Flag]:
    flags: list[Flag] = []
    if position_count == 0:
        flags.append(Flag(
            code="empty_portfolio", level="info",
            title="Tu cartera no tiene posiciones cargadas",
            detail=(
                "La diversificación y la sugerencia miden contra una cartera "
                "vacía: todo sector parece «nuevo». Carga tus posiciones reales "
                "para que esas señales signifiquen algo."
            ),
        ))
        return flags

    weight = (op.sector_weight_pct or 0.0) / 100.0
    if weight > threshold:
        flags.append(Flag(
            code="sector_concentration", level="yellow",
            title=f"Ya pesas {weight * 100:.0f}% en «{op.exposure_bucket}»",
            detail=(
                "Otra compra en el mismo sector añade el mismo riesgo que ya "
                "tienes: si el sector cae, cae todo junto."
            ),
            evidence={"sector_weight_pct": op.sector_weight_pct, "threshold_pct": threshold * 100},
        ))
    return flags


# ---------------------------------------------------------------------------
# Veredicto
# ---------------------------------------------------------------------------


def summarize(flags: list[Flag]) -> Verdict:
    """Cuenta las banderas y fija el semáforo. Una roja basta para el rojo."""
    counts = {level: sum(1 for f in flags if f.level == level)
              for level in ("red", "yellow", "green", "info")}

    if counts["red"]:
        level, label = "red", "Hay banderas rojas"
    elif counts["yellow"]:
        level, label = "yellow", "Revisa estos puntos antes de decidir"
    else:
        level, label = "green", "Sin banderas rojas ni amarillas"

    return Verdict(level=level, label=label, **counts)
