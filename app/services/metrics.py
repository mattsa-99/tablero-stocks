"""Indicadores técnicos y estadísticos sobre series de precios.

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

TRADING_DAYS_YEAR = 252
TRADING_DAYS_MONTH = 21

SMA_FAST = 50
SMA_SLOW = 200

# Ventanas mínimas por debajo de las cuales la métrica es ruido, no señal.
MIN_BARS_VOLATILITY = 60
MIN_BARS_DRAWDOWN = 60


def sma(prices: Sequence[float], window: int) -> float | None:
    """Media móvil simple de las últimas `window` barras."""
    if len(prices) < window or window <= 0:
        return None
    return sum(prices[-window:]) / window


def sma_trend(prices: Sequence[float]) -> float | None:
    """SMA50 / SMA200 - 1.

    Positivo = la media rápida está por encima de la lenta (cruce dorado).
    Se expresa como desviación relativa y no como booleano de cruce para
    conservar la MAGNITUD de la tendencia, que es lo que el rango percentil
    necesita para ordenar.
    """
    fast = sma(prices, SMA_FAST)
    slow = sma(prices, SMA_SLOW)
    if fast is None or slow is None or slow == 0:
        return None
    return fast / slow - 1.0


def momentum_12_1(prices: Sequence[float]) -> float | None:
    """Momentum 12-1 de Jegadeesh-Titman: retorno a 12 meses SIN el último mes.

    Excluir el mes más reciente no es un detalle de implementación: ese tramo
    exhibe reversión a corto plazo, y al incluirlo se contamina la señal con
    el ruido que precisamente la contradice.
    """
    if len(prices) < TRADING_DAYS_YEAR:
        return None
    recent = prices[-TRADING_DAYS_MONTH]
    old = prices[-TRADING_DAYS_YEAR]
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
    prices: Sequence[float], window: int = TRADING_DAYS_YEAR
) -> float | None:
    """Desviación típica de los retornos diarios, anualizada por sqrt(252)."""
    series = prices[-(window + 1) :]
    if len(series) < MIN_BARS_VOLATILITY:
        return None
    returns = daily_log_returns(series)
    if len(returns) < 2:
        return None

    mean = sum(returns) / len(returns)
    # Varianza muestral (n-1): estimamos sobre una muestra, no sobre la población.
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(TRADING_DAYS_YEAR)


def max_drawdown(prices: Sequence[float], window: int = TRADING_DAYS_YEAR) -> float | None:
    """Máxima caída desde un pico previo, en [0, 1].

    Complementa a la volatilidad: dos activos pueden tener la misma sigma y
    perfiles de pérdida muy distintos si uno cae de forma sostenida.
    """
    series = prices[-window:]
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
