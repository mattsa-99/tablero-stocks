"""Indicadores técnicos y estadísticos sobre series de precios y sobre pesos.

Funciones puras sobre listas de floats ORDENADAS ASCENDENTEMENTE por fecha.
Sin pandas ni numpy: las series son de cientos de puntos, la aritmética es
trivial y evitar la dependencia mantiene el motor cuantitativo testeable de
forma aislada.

IMPORTANTE: alimentar siempre con `adj_close` cuando exista. Con `close` sin
ajustar, un split 2:1 aparece como una caída del 50% y tanto el momentum como
la volatilidad quedan invertidos o inflados.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal

TRADING_DAYS_YEAR = 252
CALENDAR_DAYS_YEAR = 365

TRADING_DAYS_MONTH = 21

SMA_FAST = 50
SMA_SLOW = 200

# Las ventanas se expresan como FRACCIÓN DE AÑO, no como un número fijo de
# barras, y esto es el cambio de fondo de este módulo.
#
# EL FALLO QUE CORRIGE. Todo asumía 252 barras por año, que es lo que tiene una
# bolsa. El cripto cotiza los 365 días, así que sobre la misma serie:
#
#   - la volatilidad se anualizaba con sqrt(252) en vez de sqrt(365) y salía
#     un 20,5% por debajo. Medido sobre la base real: BTC-USD aparecía con un
#     39,1% cuando su volatilidad es del 47,1%; ETH y SOL, igual de desviados.
#   - el «momentum 12-1» miraba 252 barras atrás, que en cripto son 252 días
#     naturales: cubría 231 días (7,6 meses) frente a los 336 (11 meses) de
#     una acción. No era el mismo indicador.
#
# Nada de esto lanzaba un error: el cripto competía en el mismo rango
# percentil que las acciones con un riesgo rebajado y un horizonte distinto.
MONTH_FRACTION = 1 / 12
SMA_FAST_FRACTION = SMA_FAST / TRADING_DAYS_YEAR
SMA_SLOW_FRACTION = SMA_SLOW / TRADING_DAYS_YEAR

# Ventanas mínimas por debajo de las cuales la métrica es ruido, no señal.
MIN_BARS_VOLATILITY = 60
MIN_BARS_DRAWDOWN = 60


def _bars(fraction: float, periods_per_year: int) -> int:
    """Barras que cubren `fraction` de año en un mercado de esta cadencia."""
    return max(1, round(fraction * periods_per_year))


def sma(prices: Sequence[float], window: int) -> float | None:
    """Media móvil simple de las últimas `window` barras."""
    if len(prices) < window or window <= 0:
        return None
    return sum(prices[-window:]) / window


def sma_trend(
    prices: Sequence[float], periods_per_year: int = TRADING_DAYS_YEAR
) -> float | None:
    """SMA rápida / SMA lenta - 1, sobre ventanas equivalentes en TIEMPO.

    Positivo = la media rápida está por encima de la lenta (cruce dorado).
    Se expresa como desviación relativa y no como booleano de cruce para
    conservar la MAGNITUD de la tendencia, que es lo que el rango percentil
    necesita para ordenar.

    Las clásicas 50 y 200 sesiones son 2,4 y 9,5 meses en una bolsa. En un
    mercado que abre los 365 días serían 1,6 y 6,6: otro indicador con el
    mismo nombre. Se escalan para que la pregunta sea la misma.
    """
    fast = sma(prices, _bars(SMA_FAST_FRACTION, periods_per_year))
    slow = sma(prices, _bars(SMA_SLOW_FRACTION, periods_per_year))
    if fast is None or slow is None or slow == 0:
        return None
    return fast / slow - 1.0


def momentum_12_1(
    prices: Sequence[float], periods_per_year: int = TRADING_DAYS_YEAR
) -> float | None:
    """Momentum 12-1 de Jegadeesh-Titman: retorno a 12 meses SIN el último mes.

    Excluir el mes más reciente no es un detalle de implementación: ese tramo
    exhibe reversión a corto plazo, y al incluirlo se contamina la señal con
    el ruido que precisamente la contradice.

    «Doce meses» son doce meses de calendario, no 252 barras: en un mercado
    que abre a diario, 252 barras son ocho meses.
    """
    year = periods_per_year
    month = _bars(MONTH_FRACTION, periods_per_year)
    if len(prices) < year:
        return None
    recent = prices[-month]
    old = prices[-year]
    if old <= 0:
        return None
    return recent / old - 1.0


def daily_log_returns(prices: Sequence[float]) -> list[float]:
    """Retornos logarítmicos. Aditivos en el tiempo, a diferencia de los simples."""
    returns = []
    for previous, current in zip(prices, prices[1:], strict=False):
        if previous > 0 and current > 0:
            returns.append(math.log(current / previous))
    return returns


def annualized_volatility(
    prices: Sequence[float],
    window: int | None = None,
    periods_per_year: int = TRADING_DAYS_YEAR,
) -> float | None:
    """Desviación típica de los retornos diarios, anualizada.

    El factor es sqrt(barras por año) y NO sqrt(252) fijo. Con 252 sobre una
    serie de 365 barras, la cifra sale un 20,5% por debajo de la real: es el
    error que hacía aparecer a BTC-USD con un 39,1% de volatilidad cuando la
    suya es del 47,1%.
    """
    window = periods_per_year if window is None else window
    series = prices[-(window + 1) :]
    if len(series) < MIN_BARS_VOLATILITY:
        return None
    returns = daily_log_returns(series)
    if len(returns) < 2:
        return None

    mean = sum(returns) / len(returns)
    # Varianza muestral (n-1): estimamos sobre una muestra, no sobre la población.
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(periods_per_year)


def max_drawdown(prices: Sequence[float], window: int | None = None) -> float | None:
    """Máxima caída desde un pico previo, en [0, 1].

    Complementa a la volatilidad: dos activos pueden tener la misma sigma y
    perfiles de pérdida muy distintos si uno cae de forma sostenida.

    SIN VENTANA POR DEFECTO, y es un cambio deliberado. Antes eran 252 barras
    -un año- porque era todo lo que había guardado. Pero la pregunta que esta
    métrica responde es «¿cuánto ha llegado a caer esto?», y acotarla a un año
    la convierte en «¿cuánto cayó el año pasado?», que no es lo mismo ni sirve
    para dimensionar una posición: un activo tranquilo doce meses puede haber
    caído un 60% dieciocho meses atrás.

    Medido sobre la base real con la ventana de 400 días: NINGUNO de los 711
    activos llegaba a dos años de histórico. Por eso `sizing.py` necesita un
    suelo de estrés del 35%: no era prudencia, era tapar la falta de datos.
    Cuanto más hondo llegue la serie, menos trabajo hace ese suelo.
    """
    series = prices if window is None else prices[-window:]
    if len(series) < MIN_BARS_DRAWDOWN:
        return None

    peak = series[0]
    worst = 0.0
    for price in series:
        peak = max(peak, price)
        if peak > 0:
            worst = max(worst, 1.0 - price / peak)
    return worst


def percent_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return (current / previous - 1.0) * 100.0


# ---------------------------------------------------------------------------
# Concentración. Vive aquí -y no en el simulador, donde nació- porque la mide
# también el resumen de la cartera: dos copias acabarían diciendo cosas
# distintas sobre la misma cartera en dos pantallas.
# ---------------------------------------------------------------------------


def herfindahl(weights: list[Decimal]) -> Decimal:
    """Índice Herfindahl-Hirschman sobre pesos que suman 1.

    HHI = Σ wᵢ². Vale 1 con todo en un solo sitio y 1/n con n partes iguales,
    así que mide concentración de forma continua en lugar de "¿supera el 30%?".
    """
    return sum((w * w for w in weights), Decimal(0))


def diversification_index(weights: list[Decimal]) -> Decimal | None:
    """Índice de diversificación 0-100 = 100 · (1 − HHI).

    Interpretación honesta: es una escala RELATIVA, pensada para comparar el
    antes y el después de una misma cartera —que es exactamente el trabajo del
    simulador—, no una nota absoluta. Con los 11 sectores GICS el techo real es
    100·(1−1/11) ≈ 90,9: el 100 es inalcanzable por construcción, así que un 72
    no significa "aprobado raspado".

    Se elige HHI y no el conteo de sectores porque este último no distingue una
    cartera con 5 sectores al 20% de otra con 5 donde uno pesa el 96%.
    """
    if not weights:
        return None
    return (Decimal("1") - herfindahl(weights)) * Decimal(100)
