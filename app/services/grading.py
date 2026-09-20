"""Calificación ABSOLUTA de un activo. Independiente del universo evaluado.

POR QUÉ EXISTE ESTE MÓDULO
==========================
El Opportunity Score usa rangos percentiles, que son puramente ordinales: dicen
quién es mejor DENTRO del conjunto evaluado y nada más. Su punto ciego es real
y peligroso: un universo de 27 acciones malas produce igualmente un primer
puesto con score alto, y el número invita a leerlo como "compra".

La calificación de este módulo no mira a los demás candidatos. Compara cada
activo contra ANCLAS que no dependen del conjunto:

  1. El mercado (el P/E del índice de referencia), no los otros candidatos.
  2. Umbrales absolutos de riesgo: una volatilidad anualizada del 60% es alta
     para una acción, mire uno lo que mire.
  3. El signo de la tendencia, que es absoluto por definición.
  4. Rentabilidad del negocio contra umbrales contables convencionales.

Las dos cifras responden preguntas distintas y se muestran juntas:
  score  -> "¿cuál de estos es el mejor?"
  grado  -> "¿es bueno en términos absolutos?"

LO QUE ESTO NO ES
=================
Una heurística explicable, no un modelo validado. Los umbrales son convenciones
razonadas, no el resultado de un backtest. Un grado A significa "cumple varios
criterios objetivos de calidad, valoración, tendencia y riesgo", no "va a
subir".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

# Escala de la deuda en yfinance: `debtToEquity` viene en PORCENTAJE
# (380.26 = 3,8x), mientras que ROE, margen y crecimiento son fracciones
# (0.1779 = 17,79%). Confundirlas invalida todos los umbrales.
DEBT_TO_EQUITY_IS_PERCENT = True


class Grade(StrEnum):
    EXCELLENT = "A"
    GOOD = "B"
    NEUTRAL = "C"
    POOR = "D"
    BAD = "E"
    UNRATED = "SIN_CALIFICAR"


GRADE_LABEL = {
    Grade.EXCELLENT: "Muy buena",
    Grade.GOOD: "Buena",
    Grade.NEUTRAL: "Normal",
    Grade.POOR: "Mala",
    Grade.BAD: "Muy mala",
    Grade.UNRATED: "Sin calificar",
}

# Identificador estable de cada calificación para la API y la interfaz.
#
# Se deriva de la etiqueta y no del código A-E porque es lo que el usuario ve
# y lo que la URL vuelve legible (`?quality_tiers=muy_buena,buena`). El código
# sigue siendo la clave interna: esto es solo su nombre público.
GRADE_SLUG: dict[Grade, str] = {
    Grade.EXCELLENT: "muy_buena",
    Grade.GOOD: "buena",
    Grade.NEUTRAL: "normal",
    Grade.POOR: "mala",
    Grade.BAD: "muy_mala",
    Grade.UNRATED: "sin_calificar",
}

SLUG_TO_GRADE: dict[str, Grade] = {slug: grade for grade, slug in GRADE_SLUG.items()}


def parse_quality_tiers(raw: str | None) -> set[Grade] | None:
    """`"muy_buena,buena"` -> {A, B}. None si no se pidió filtrar.

    Un slug desconocido se ignora en vez de devolver 422: el filtro es una
    comodidad de la vista y fallar dejaría la pantalla en blanco por un
    parámetro de interfaz, no por un problema con los datos.
    """
    if not raw or not raw.strip():
        return None
    wanted = {
        SLUG_TO_GRADE[token]
        for token in (t.strip().lower() for t in raw.split(","))
        if token in SLUG_TO_GRADE
    }
    return wanted or None


# Hacen falta al menos 2 de las 4 señales con datos. Con una sola, la suma
# tiende al centro y todo saldría "Normal", que es afirmar algo sin base.
MIN_SIGNALS_FOR_GRADE = 2


@dataclass
class Signal:
    """Una señal absoluta: puntúa de -2 a +2, o None si no hay datos."""

    name: str
    label: str
    points: int | None
    detail: str
    inputs: dict[str, float | None] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return self.points is not None


@dataclass
class Assessment:
    grade: Grade
    label: str
    points: int
    max_points: int
    signals: list[Signal]
    notes: list[str] = field(default_factory=list)

    @property
    def available_signals(self) -> int:
        return sum(1 for s in self.signals if s.available)


# ---------------------------------------------------------------------------
# Señal 1: valoración contra el MERCADO, no contra los otros candidatos
# ---------------------------------------------------------------------------


def _valuation_signal(
    trailing_pe: float | None,
    forward_pe: float | None,
    price_to_book: float | None,
    market_pe: float | None,
    sector_trailing_pe: float | None = None,
    sector_forward_pe: float | None = None,
    sector_name: str | None = None,
) -> Signal:
    inputs = {
        "trailing_pe": trailing_pe,
        "forward_pe": forward_pe,
        "price_to_book": price_to_book,
        "market_pe": market_pe,
    }

    # El orden importa. Se prefiere un P/E POSITIVO, sea trailing o forward:
    # una empresa que hoy pierde pero se espera que gane tiene información en
    # el forward que el trailing negativo no da.
    #
    # Solo si NINGUNO es positivo se emite la señal de pérdidas. Una versión
    # anterior hacía `trailing if trailing > 0 else forward`, y con trailing
    # negativo y forward ausente acababa en "sin datos": las pérdidas quedaban
    # como una carencia de información en lugar de como la señal negativa que
    # son.
    pe = None
    sector_pe = None
    if (trailing_pe or 0) > 0:
        pe = trailing_pe
        sector_pe = sector_trailing_pe
    elif (forward_pe or 0) > 0:
        pe = forward_pe
        sector_pe = sector_forward_pe
    elif trailing_pe is not None or forward_pe is not None:
        return Signal(
            "valuation", "Valoración", -1,
            "La empresa no tiene beneficios positivos", inputs,
        )

    # El ancla es la mediana de SU SECTOR cuando existe, y el índice solo como
    # respaldo. Un banco a P/E 9,6 «cotiza al 39% del mercado» y parece una
    # ganga, pero los bancos cotizan así casi siempre: contra otros bancos la
    # misma cifra puede ser exactamente lo normal. Se compara trailing con
    # trailing y forward con forward: mezclarlos sesgaría a favor del forward,
    # que casi siempre es menor.
    use_sector = sector_pe is not None and sector_pe > 0
    anchor = sector_pe if use_sector else market_pe
    reference = f"de su sector ({sector_name})" if use_sector and sector_name else (
        "de su sector" if use_sector else "del mercado"
    )
    inputs["reference_pe"] = anchor

    if pe is None or anchor is None or anchor <= 0:
        return Signal(
            "valuation", "Valoración", None,
            "Sin P/E propio o sin referencia de mercado", inputs,
        )

    ratio = pe / anchor
    # UNA sola clave. Antes se escribían `pe_vs_market` y `pe_vs_reference` con
    # el MISMO valor, y el nombre viejo mentía en cuanto el ancla pasó a ser la
    # mediana del sector: decía "frente al mercado" sobre una cifra que ya no
    # se calculaba contra el mercado. `reference_pe` de arriba dice cuál es el
    # ancla usada, así que la pareja se lee sola.
    inputs["pe_vs_reference"] = round(ratio, 3)

    if ratio < 0.7:
        return Signal("valuation", "Valoración", 2,
                      f"Cotiza a {ratio:.0%} del P/E {reference}: muy por debajo", inputs)
    if ratio < 0.9:
        return Signal("valuation", "Valoración", 1,
                      f"Cotiza a {ratio:.0%} del P/E {reference}: por debajo", inputs)
    if ratio <= 1.3:
        return Signal("valuation", "Valoración", 0,
                      f"Cotiza a {ratio:.0%} del P/E {reference}: en línea", inputs)
    if ratio <= 1.8:
        return Signal("valuation", "Valoración", -1,
                      f"Cotiza a {ratio:.0%} del P/E {reference}: cara", inputs)
    return Signal("valuation", "Valoración", -2,
                  f"Cotiza a {ratio:.0%} del P/E {reference}: muy cara", inputs)


# ---------------------------------------------------------------------------
# Señal 2: tendencia. El signo de una tendencia es absoluto por definición
# ---------------------------------------------------------------------------


def _trend_signal(sma_trend: float | None, momentum_12_1: float | None) -> Signal:
    inputs = {"sma50_over_sma200_minus_1": sma_trend, "momentum_12_1": momentum_12_1}

    if sma_trend is None and momentum_12_1 is None:
        return Signal("trend", "Tendencia", None, "Sin histórico suficiente", inputs)

    score = 0.0
    parts: list[str] = []

    if sma_trend is not None:
        if sma_trend > 0.05:
            score += 1.0
            parts.append("media de 50 sesiones muy por encima de la de 200")
        elif sma_trend > 0:
            score += 0.5
            parts.append("media de 50 por encima de la de 200")
        elif sma_trend > -0.05:
            score -= 0.5
            parts.append("media de 50 por debajo de la de 200")
        else:
            score -= 1.0
            parts.append("media de 50 muy por debajo de la de 200")

    if momentum_12_1 is not None:
        if momentum_12_1 > 0.20:
            score += 1.0
            parts.append(f"sube {momentum_12_1:.0%} en el año")
        elif momentum_12_1 > 0:
            score += 0.5
            parts.append(f"sube {momentum_12_1:.0%} en el año")
        elif momentum_12_1 > -0.20:
            score -= 0.5
            parts.append(f"cae {abs(momentum_12_1):.0%} en el año")
        else:
            score -= 1.0
            parts.append(f"cae {abs(momentum_12_1):.0%} en el año")

    points = max(-2, min(2, round(score * (2 / 2))))
    return Signal("trend", "Tendencia", int(points), "; ".join(parts).capitalize(), inputs)


# ---------------------------------------------------------------------------
# Señal 3: riesgo contra umbrales fijos, no contra los otros candidatos
# ---------------------------------------------------------------------------


def _risk_signal(volatility: float | None, max_drawdown: float | None) -> Signal:
    inputs = {"annualized_volatility": volatility, "max_drawdown": max_drawdown}

    if volatility is None and max_drawdown is None:
        return Signal("risk", "Riesgo", None, "Sin histórico suficiente", inputs)

    score = 0.0
    parts: list[str] = []

    # Bandas convencionales para renta variable. Una volatilidad anualizada del
    # 60% es alta se mire lo que se mire; eso es lo que las hace absolutas.
    if volatility is not None:
        if volatility < 0.20:
            score += 1.0
            parts.append(f"volatilidad baja ({volatility:.0%})")
        elif volatility < 0.35:
            parts.append(f"volatilidad moderada ({volatility:.0%})")
        elif volatility < 0.50:
            score -= 0.5
            parts.append(f"volatilidad alta ({volatility:.0%})")
        else:
            score -= 1.0
            parts.append(f"volatilidad muy alta ({volatility:.0%})")

    if max_drawdown is not None:
        if max_drawdown < 0.20:
            score += 1.0
            parts.append(f"caída máxima contenida ({max_drawdown:.0%})")
        elif max_drawdown < 0.35:
            parts.append(f"caída máxima moderada ({max_drawdown:.0%})")
        elif max_drawdown < 0.50:
            score -= 0.5
            parts.append(f"caída máxima severa ({max_drawdown:.0%})")
        else:
            score -= 1.0
            parts.append(f"caída máxima muy severa ({max_drawdown:.0%})")

    return Signal("risk", "Riesgo", int(max(-2, min(2, round(score)))),
                  "; ".join(parts).capitalize(), inputs)


# ---------------------------------------------------------------------------
# Señal 4: calidad del negocio. Umbrales contables convencionales
# ---------------------------------------------------------------------------


def _quality_signal(
    roe: float | None,
    profit_margin: float | None,
    debt_to_equity: float | None,
    revenue_growth: float | None,
) -> Signal:
    inputs = {
        "return_on_equity": roe,
        "profit_margin": profit_margin,
        "debt_to_equity": debt_to_equity,
        "revenue_growth": revenue_growth,
    }

    measured = [v for v in (roe, profit_margin, debt_to_equity, revenue_growth) if v is not None]
    if not measured:
        # Es el caso normal de ETFs y futuros: no tienen estados financieros.
        return Signal("quality", "Calidad", None, "Sin estados financieros", inputs)

    score = 0.0
    parts: list[str] = []

    # ROE y margen llegan como FRACCIÓN (0.1779 = 17,79%).
    if roe is not None:
        if roe > 0.20:
            score += 1.0
            parts.append(f"ROE alto ({roe:.0%})")
        elif roe > 0.10:
            score += 0.5
            parts.append(f"ROE sólido ({roe:.0%})")
        elif roe > 0:
            parts.append(f"ROE bajo ({roe:.0%})")
        else:
            score -= 1.0
            parts.append("ROE negativo")

    if profit_margin is not None:
        if profit_margin > 0.15:
            score += 0.5
            parts.append(f"margen amplio ({profit_margin:.0%})")
        elif profit_margin > 0:
            parts.append(f"margen ajustado ({profit_margin:.0%})")
        else:
            score -= 1.0
            parts.append("pierde dinero")

    # debt_to_equity llega en PORCENTAJE (380.26 = 3,8x).
    if debt_to_equity is not None:
        ratio = debt_to_equity / 100 if DEBT_TO_EQUITY_IS_PERCENT else debt_to_equity
        inputs["debt_to_equity_x"] = round(ratio, 2)
        if ratio > 3.0:
            score -= 1.0
            parts.append(f"deuda muy alta ({ratio:.1f}x fondos propios)")
        elif ratio > 1.5:
            score -= 0.5
            parts.append(f"deuda elevada ({ratio:.1f}x)")
        elif ratio < 0.5:
            score += 0.5
            parts.append(f"poca deuda ({ratio:.1f}x)")

    if revenue_growth is not None:
        if revenue_growth > 0.10:
            score += 0.5
            parts.append(f"ingresos crecen {revenue_growth:.0%}")
        elif revenue_growth < 0:
            score -= 0.5
            parts.append(f"ingresos caen {abs(revenue_growth):.0%}")

    return Signal("quality", "Calidad", int(max(-2, min(2, round(score)))),
                  "; ".join(parts).capitalize(), inputs)


# ---------------------------------------------------------------------------
# Composición
# ---------------------------------------------------------------------------


def _grade_from_points(points: int, max_points: int) -> Grade:
    """Califica por la PROPORCIÓN alcanzada, no por puntos absolutos.

    El máximo alcanzable varía con cuántas señales tengan datos: 8 para una
    acción con estados financieros, 6 para un ETF sin ellos, 4 para un futuro
    sin fundamentales ni referencia de valoración. Con umbrales absolutos, un
    futuro necesitaría 5 puntos sobre un máximo de 4 para sacar la mejor nota:
    imposible por construcción, y no por ser peor activo.

    La proporción cae en [-1, +1]. Los cortes NO son simétricos:

        A  >= +0.75     fuerte en prácticamente todo lo medido
        B  >= +0.35     claramente positivo
        C  >= -0.15     mixto o neutro
        D  >= -0.50     claramente negativo
        E  <  -0.50     malo en casi todo

    La banda "Normal" absorbe los positivos leves a propósito. Las señales
    están construidas para que CUMPLIR la convención puntúe 0 y solo
    SUPERARLA sume, así que una empresa decente cae de forma natural algo por
    encima de cero; si eso bastara para "Buena", la escala dejaría de
    discriminar.

    CALIBRACIÓN, no validación: el corte de A se subió de 0,50 a 0,75 tras ver
    que con 0,50 caían ahí 18 de 27 candidatos de un universo de grandes
    valores en tendencia alcista. Es un ajuste sobre UNA foto de UN universo
    concreto, no un umbral respaldado por backtest.
    """
    if max_points <= 0:
        return Grade.NEUTRAL

    ratio = points / max_points
    if ratio >= 0.75:
        return Grade.EXCELLENT
    if ratio >= 0.35:
        return Grade.GOOD
    if ratio >= -0.15:
        return Grade.NEUTRAL
    if ratio >= -0.5:
        return Grade.POOR
    return Grade.BAD


def assess(
    *,
    trailing_pe: float | None = None,
    forward_pe: float | None = None,
    price_to_book: float | None = None,
    market_pe: float | None = None,
    sma_trend: float | None = None,
    momentum_12_1: float | None = None,
    volatility: float | None = None,
    max_drawdown: float | None = None,
    roe: float | None = None,
    profit_margin: float | None = None,
    debt_to_equity: float | None = None,
    revenue_growth: float | None = None,
    sector_trailing_pe: float | None = None,
    sector_forward_pe: float | None = None,
    sector_name: str | None = None,
) -> Assessment:
    """Califica un activo en términos absolutos, sin mirar a otros candidatos.

    `sector_*` son opcionales: si se pasan, la valoración se ancla en la
    MEDIANA de P/E del sector de la empresa en lugar de en la del índice. Es
    seguir siendo absoluto -no depende de qué otros candidatos se estén
    mostrando ni de sus puestos-, pero contra la referencia correcta.
    """
    signals = [
        _valuation_signal(
            trailing_pe, forward_pe, price_to_book, market_pe,
            sector_trailing_pe, sector_forward_pe, sector_name,
        ),
        _trend_signal(sma_trend, momentum_12_1),
        _risk_signal(volatility, max_drawdown),
        _quality_signal(roe, profit_margin, debt_to_equity, revenue_growth),
    ]

    available = [s for s in signals if s.available]
    notes: list[str] = []

    if len(available) < MIN_SIGNALS_FOR_GRADE:
        # Sin datos NO se califica. Devolver "Normal" por defecto sería afirmar
        # algo sobre el activo sin base para hacerlo, que es justo el problema
        # que este módulo viene a resolver.
        return Assessment(
            grade=Grade.UNRATED,
            label=GRADE_LABEL[Grade.UNRATED],
            points=0,
            max_points=0,
            signals=signals,
            notes=[
                f"Solo {len(available)} de 4 señales tienen datos: no hay base "
                f"suficiente para calificar."
            ],
        )

    points = sum(s.points for s in available)
    max_points = 2 * len(available)

    if len(available) < len(signals):
        missing = [s.label for s in signals if not s.available]
        notes.append(f"Sin datos para: {', '.join(missing)}. La calificación es parcial.")

    grade = _grade_from_points(points, max_points)
    return Assessment(
        grade=grade,
        label=GRADE_LABEL[grade],
        points=points,
        max_points=max_points,
        signals=signals,
        notes=notes,
    )
