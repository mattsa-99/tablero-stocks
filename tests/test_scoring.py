"""Tests de las primitivas de normalización.

Aquí se concentra el riesgo de error silencioso del motor cuantitativo: un
rango invertido produce un ranking exactamente al revés sin que nada falle.
"""

from __future__ import annotations

import pytest

from app.services.metrics import (
    annualized_volatility,
    max_drawdown,
    momentum_12_1,
    sma,
    sma_trend,
)
from app.services.scoring import (
    average_available,
    diversification_score,
    invert,
    percentile_ranks,
)

# --------------------------------------------------------------------------
# Rangos percentiles
# --------------------------------------------------------------------------


def test_percentile_rank_is_monotonic():
    ranks = percentile_ranks([10.0, 20.0, 30.0, 40.0])
    assert ranks == sorted(ranks)
    assert ranks[0] < ranks[-1]


def test_hazen_avoids_absolute_extremes():
    """Ni 0 ni 100 exactos: afirmarían una certeza que no tenemos."""
    ranks = percentile_ranks([1.0, 2.0, 3.0, 4.0])
    assert 0 < min(ranks) < 100
    assert 0 < max(ranks) < 100
    assert min(ranks) == pytest.approx(12.5)  # 100*(1-0.5)/4
    assert max(ranks) == pytest.approx(87.5)  # 100*(4-0.5)/4


def test_ties_receive_equal_scores():
    """Dos valores iguales no pueden puntuar distinto por su posición en la lista."""
    ranks = percentile_ranks([10.0, 10.0, 20.0])
    assert ranks[0] == ranks[1]
    assert ranks[2] > ranks[0]


def test_nones_are_preserved_not_imputed():
    """La imputación es decisión del motor, no de la normalización."""
    ranks = percentile_ranks([10.0, None, 30.0])
    assert ranks[1] is None
    assert ranks[0] is not None and ranks[2] is not None


def test_all_none_returns_all_none():
    assert percentile_ranks([None, None]) == [None, None]


def test_single_value_lands_at_midpoint():
    """Con n=1 el rango es 50: no hay información para ordenar."""
    assert percentile_ranks([42.0]) == [pytest.approx(50.0)]


def test_outlier_does_not_compress_the_scale():
    """Ventaja frente a min-max: un P/E de 900 es solo 'el último'."""
    normal = percentile_ranks([10.0, 20.0, 30.0])
    with_outlier = percentile_ranks([10.0, 20.0, 900.0])
    assert normal == with_outlier


def test_invert_flips_direction():
    assert invert([12.5, 87.5]) == [87.5, 12.5]
    assert invert([None]) == [None]


def test_average_available_ignores_missing():
    assert average_available([10.0, None, 30.0]) == pytest.approx(20.0)
    assert average_available([None, None]) is None


# --------------------------------------------------------------------------
# Diversificación
# --------------------------------------------------------------------------


def test_absent_sector_scores_maximum():
    assert diversification_score(0.0) == pytest.approx(100.0)


def test_full_concentration_scores_zero():
    assert diversification_score(1.0) == pytest.approx(0.0)


def test_threshold_lands_at_90():
    assert diversification_score(0.30) == pytest.approx(90.0)


def test_penalty_slope_is_steeper_past_threshold():
    """El requisito es un QUIEBRE real en el 30%, no un descenso lineal.

    Una versión anterior descendía "suavemente" hasta 70 y luego hasta 0, y
    resultaba ser exactamente 100(1-w): perfectamente lineal, sin ningún
    efecto de umbral. Este test es el que lo detecta.
    """
    below = diversification_score(0.20) - diversification_score(0.30)
    above = diversification_score(0.30) - diversification_score(0.40)

    assert above > below * 3, "La pendiente tras el umbral debe ser mucho más severa"
    assert below == pytest.approx(3.333, abs=0.01)
    assert above == pytest.approx(12.857, abs=0.01)


def test_diversification_is_monotonically_decreasing():
    scores = [diversification_score(w / 100) for w in range(0, 101)]
    assert all(a >= b for a, b in zip(scores, scores[1:], strict=False))


def test_weight_is_clamped():
    assert diversification_score(-0.5) == pytest.approx(100.0)
    assert diversification_score(2.0) == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Indicadores técnicos
# --------------------------------------------------------------------------


def test_sma_needs_enough_bars():
    assert sma([1.0, 2.0], 5) is None
    assert sma([1.0, 2.0, 3.0], 3) == pytest.approx(2.0)


def test_sma_trend_positive_in_uptrend():
    prices = [100.0 * (1.003**i) for i in range(250)]
    trend = sma_trend(prices)
    assert trend is not None and trend > 0


def test_sma_trend_negative_in_downtrend():
    prices = [100.0 * (0.997**i) for i in range(250)]
    trend = sma_trend(prices)
    assert trend is not None and trend < 0


def test_sma_trend_none_without_200_bars():
    assert sma_trend([100.0] * 199) is None


def test_momentum_excludes_last_month():
    """El 12-1 mira P[-21]/P[-252], no P[-1]/P[-252].

    Serie plana salvo un desplome en los últimos 10 días: el momentum debe
    seguir siendo ~0, porque ese tramo está fuera de la ventana.
    """
    prices = [100.0] * 252 + [50.0] * 10
    momentum = momentum_12_1(prices)
    assert momentum == pytest.approx(0.0, abs=1e-9)


def test_momentum_captures_the_year():
    prices = [100.0] * 21 + [200.0] * 231
    momentum = momentum_12_1(prices)
    assert momentum == pytest.approx(1.0)


def test_momentum_none_without_a_year():
    assert momentum_12_1([100.0] * 251) is None


def test_volatility_is_zero_for_flat_series():
    assert annualized_volatility([100.0] * 100) == pytest.approx(0.0)


def test_volatility_grows_with_noise():
    calm = [100.0 * (1.0001**i) for i in range(200)]
    wild = [100.0 * (1.0001**i) * (1.1 if i % 2 else 0.9) for i in range(200)]
    assert annualized_volatility(wild) > annualized_volatility(calm)


def test_volatility_none_below_minimum_window():
    assert annualized_volatility([100.0] * 30) is None


def test_crypto_volatility_is_annualised_over_365_days():
    """El fallo medido: BTC salía con 39,1% cuando su volatilidad es 47,1%.

    Misma serie, dos anualizaciones. Con 252 sobre un mercado que abre los 365
    días, la cifra sale sqrt(252/365) = 0,831 veces la real, o sea un 20,5%
    por debajo. Y entraba en el mismo rango percentil que las acciones.
    """
    import math
    import random

    random.seed(7)
    serie = [100.0]
    for _ in range(400):
        serie.append(serie[-1] * math.exp(random.gauss(0, 0.03)))

    # Misma ventana en las dos para aislar el factor de anualización: la
    # ventana por defecto también cambia con la cadencia, y eso se comprueba
    # aparte.
    bolsa = annualized_volatility(serie, window=300, periods_per_year=252)
    cripto = annualized_volatility(serie, window=300, periods_per_year=365)
    assert cripto == pytest.approx(bolsa * math.sqrt(365 / 252), rel=1e-9)
    assert cripto / bolsa == pytest.approx(1.204, abs=0.001)

    # Y la ventana por defecto sí es un año de ESE mercado: 366 barras contra
    # 253. Dos cosas distintas que antes estaban pegadas a la misma constante.
    assert annualized_volatility(serie[:253], periods_per_year=252) is not None
    assert annualized_volatility(serie[:253], periods_per_year=365) is not None


def test_crypto_momentum_covers_twelve_real_months():
    """«12-1» son doce meses de calendario, no 252 barras.

    En una serie diaria de 365 barras al año, mirar 252 atrás son ocho meses.
    El indicador dejaba de ser el que dice su nombre justo en la clase más
    volátil del universo.
    """
    # Serie que sube de forma constante: el retorno medido delata la ventana.
    serie = [100.0 * (1.001**i) for i in range(400)]

    bolsa = momentum_12_1(serie, 252)
    cripto = momentum_12_1(serie, 365)
    assert cripto > bolsa, "Doce meses de calendario abarcan más subida que 252 barras"
    # 365 - 30 = 335 barras de subida compuesta frente a 252 - 21 = 231.
    assert cripto == pytest.approx(1.001**335 - 1, rel=1e-6)
    assert bolsa == pytest.approx(1.001**231 - 1, rel=1e-6)


def test_max_drawdown_measures_peak_to_trough():
    prices = [100.0] * 30 + [50.0] * 30 + [80.0] * 30
    assert max_drawdown(prices) == pytest.approx(0.5)


def test_max_drawdown_zero_when_monotonic():
    assert max_drawdown([100.0 + i for i in range(100)]) == pytest.approx(0.0)
