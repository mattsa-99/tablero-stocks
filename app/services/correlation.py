"""Correlación y riesgo marginal de cartera.

Matemática pura sobre series de precios: sin BD, sin red, sin pandas. Las
series son de cientos de puntos y el álgebra es elemental, así que la
dependencia no compensa y el módulo queda testeable en aislamiento.

LA ALINEACIÓN NO ES UN DETALLE
==============================
Dos activos casi nunca tienen las mismas fechas: cotizan en bolsas distintas,
con festivos distintos, y las criptomonedas cotizan también en fin de semana.
Calcular la correlación emparejando por POSICIÓN en la lista, en lugar de por
FECHA, produce un número perfectamente plausible y completamente falso. Todo
aquí trabaja sobre el conjunto de fechas COMUNES.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

# Por debajo de esto la correlación es ruido: con 30 puntos, dos series
# aleatorias alcanzan |r| > 0,35 con facilidad.
MIN_OVERLAP = 60

TRADING_DAYS_YEAR = 252


@dataclass
class VolatilityImpact:
    """Efecto de añadir un activo sobre la volatilidad de la cartera."""

    current_volatility: float
    simulated_volatility: float
    delta_pct: float  # variación relativa en %; negativa = reduce riesgo
    correlation: float
    weight: float
    overlap_days: int

    # Los tres valores que se ENSEÑAN, ya redondeados a un decimal. El delta se
    # deriva de esos mismos redondeos y no de la precisión interna: si en
    # pantalla se ve "de 29,7% a 27,9%", la bajada tiene que ser 1,8 puntos
    # exactos, no 1,87 que el ojo no puede cuadrar con lo que lee.
    @property
    def current_pct(self) -> float:
        return round(self.current_volatility * 100, 1)

    @property
    def simulated_pct(self) -> float:
        return round(self.simulated_volatility * 100, 1)

    @property
    def delta_pp(self) -> float:
        """Cambio en puntos porcentuales. Negativo = baja el riesgo."""
        return round(self.simulated_pct - self.current_pct, 1)


def to_returns(prices: list[float]) -> list[float]:
    """Retornos simples entre precios consecutivos."""
    return [
        (current / previous) - 1.0
        for previous, current in zip(prices, prices[1:], strict=False)
        if previous > 0
    ]


def align_returns(
    series: dict[int, list[tuple[dt.date, float]]],
) -> tuple[list[dt.date], dict[int, list[float]]]:
    """Alinea varias series por FECHA y devuelve sus retornos.

    Solo entran las fechas presentes en TODAS las series. Es conservador -una
    cripto que cotiza en fin de semana pierde esos días al compararse con una
    acción- pero la alternativa, rellenar huecos, inventaría retornos de cero
    que sesgan la correlación hacia arriba.
    """
    if not series:
        return [], {}

    by_asset = {asset_id: dict(rows) for asset_id, rows in series.items()}
    common: set[dt.date] | None = None
    for dates in by_asset.values():
        common = set(dates) if common is None else common & set(dates)

    ordered = sorted(common or [])
    if len(ordered) < 2:
        return [], {}

    returns: dict[int, list[float]] = {}
    for asset_id, prices in by_asset.items():
        ordered_prices = [prices[day] for day in ordered]
        returns[asset_id] = to_returns(ordered_prices)

    # Los retornos tienen un elemento menos que las fechas.
    return ordered[1:], returns


def returns_by_date(prices: list[tuple[dt.date, float]]) -> dict[dt.date, float]:
    """Retornos indexados POR FECHA.

    Indexar por fecha y no por posición es lo que permite correlacionar dos
    activos con calendarios distintos sin emparejar el martes de uno con el
    miércoles del otro: un error que produce un número plausible y falso.
    """
    ordered = sorted(prices)
    result: dict[dt.date, float] = {}
    for (_, previous), (day, current) in zip(ordered, ordered[1:], strict=False):
        if previous > 0:
            result[day] = (current / previous) - 1.0
    return result


def paired(
    left: dict[dt.date, float], right: dict[dt.date, float]
) -> tuple[list[float], list[float]]:
    """Extrae los valores de las fechas COMUNES, en orden cronológico."""
    common = sorted(set(left) & set(right))
    return [left[day] for day in common], [right[day] for day in common]


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: list[float]) -> float:
    """Desviación típica MUESTRAL (n-1): se estima sobre una muestra."""
    if len(values) < 2:
        return 0.0
    average = mean(values)
    variance = sum((v - average) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def pearson(left: list[float], right: list[float]) -> float | None:
    """Correlación de Pearson. None si no hay solape suficiente o no varían.

    Devolver None y no 0 cuando falta información es deliberado: un 0 significa
    "no correlacionados", que es una afirmación fuerte y justamente la que el
    motor de sugerencia premia. Confundirla con "no lo sé" recomendaría activos
    por una descorrelación que nadie ha medido.
    """
    size = min(len(left), len(right))
    if size < MIN_OVERLAP:
        return None

    left, right = left[-size:], right[-size:]
    left_mean, right_mean = mean(left), mean(right)

    covariance = sum(
        (a - left_mean) * (b - right_mean) for a, b in zip(left, right, strict=True)
    )
    left_ss = sum((a - left_mean) ** 2 for a in left)
    right_ss = sum((b - right_mean) ** 2 for b in right)

    if left_ss <= 0 or right_ss <= 0:
        return None  # una serie constante no tiene correlación definida

    return covariance / math.sqrt(left_ss * right_ss)


def annualized(daily_stdev: float) -> float:
    return daily_stdev * math.sqrt(TRADING_DAYS_YEAR)


def portfolio_returns(
    weights: dict[int, float], returns: dict[int, list[float]]
) -> list[float]:
    """Serie de retornos de la cartera: suma ponderada de sus posiciones.

    Los pesos se renormalizan sobre los activos que SÍ tienen serie. Sin eso,
    una posición sin histórico haría que los pesos sumaran menos de 1 y la
    volatilidad de la cartera saldría artificialmente baja.
    """
    usable = {
        asset_id: weight
        for asset_id, weight in weights.items()
        if returns.get(asset_id)
    }
    total = sum(usable.values())
    if not usable or total <= 0:
        return []

    length = min(len(returns[asset_id]) for asset_id in usable)
    if length < 2:
        return []

    combined = []
    for index in range(length):
        combined.append(
            sum(
                (weight / total) * returns[asset_id][-length:][index]
                for asset_id, weight in usable.items()
            )
        )
    return combined


def volatility_impact(
    base_returns: list[float],
    candidate_returns: list[float],
    weight: float,
) -> VolatilityImpact | None:
    """Volatilidad de la cartera al añadir `weight` del candidato.

    Fórmula estándar de varianza de una cartera de dos componentes, donde uno
    es la cartera actual tratada como un solo activo:

        sigma_nueva^2 = (1-w)^2·sigma_p^2 + w^2·sigma_c^2
                        + 2·w·(1-w)·rho·sigma_p·sigma_c

    El término cruzado es lo que hace que un activo POCO correlacionado pueda
    bajar la volatilidad total aun siendo más volátil que la cartera: si rho es
    bajo o negativo, ese sumando resta.
    """
    size = min(len(base_returns), len(candidate_returns))
    if size < MIN_OVERLAP:
        return None

    base = base_returns[-size:]
    candidate = candidate_returns[-size:]

    rho = pearson(base, candidate)
    if rho is None:
        return None

    sigma_p = stdev(base)
    sigma_c = stdev(candidate)
    if sigma_p <= 0:
        return None

    variance = (
        (1 - weight) ** 2 * sigma_p**2
        + weight**2 * sigma_c**2
        + 2 * weight * (1 - weight) * rho * sigma_p * sigma_c
    )
    sigma_new = math.sqrt(max(0.0, variance))

    return VolatilityImpact(
        current_volatility=annualized(sigma_p),
        simulated_volatility=annualized(sigma_new),
        delta_pct=(sigma_new / sigma_p - 1.0) * 100.0,
        correlation=rho,
        weight=weight,
        overlap_days=size,
    )
