"""Primitivas de normalización del motor cuantitativo.

Aisladas de `opportunities.py` porque son puramente matemáticas y es donde se
concentra el riesgo de error silencioso: un rango invertido produce un ranking
exactamente al revés sin que nada falle.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from statistics import median

NEUTRAL_SCORE = 50.0


def percentile_ranks(values: Sequence[float | None]) -> list[float | None]:
    """Rango percentil de Hazen: 100 * (r - 0.5) / n.

    - Los None se preservan como None (no se imputan aquí).
    - Los empates reciben el rango PROMEDIO, de forma que dos valores iguales
      nunca obtienen scores distintos por su posición en la lista.
    - El -0.5 evita asignar exactamente 0 o 100 a los extremos: un 100 absoluto
      afirmaría certeza sobre el mejor activo del universo, y con 20 candidatos
      eso es una precisión que no tenemos.

    Más alto = mayor valor original. Para métricas donde "menor es mejor"
    (P/E, volatilidad) usar `invert()` sobre el resultado.
    """
    indexed = [(value, i) for i, value in enumerate(values) if value is not None]
    result: list[float | None] = [None] * len(values)
    n = len(indexed)
    if n == 0:
        return result

    indexed.sort(key=lambda pair: pair[0])

    position = 0
    while position < n:
        end = position
        while end + 1 < n and indexed[end + 1][0] == indexed[position][0]:
            end += 1
        # Rangos 1-based promediados sobre el bloque de empates.
        average_rank = (position + 1 + end + 1) / 2
        score = 100.0 * (average_rank - 0.5) / n
        for offset in range(position, end + 1):
            result[indexed[offset][1]] = score
        position = end + 1

    return result


def invert(scores: Sequence[float | None]) -> list[float | None]:
    """Convierte "mayor es mejor" en "menor es mejor" y viceversa."""
    return [None if s is None else 100.0 - s for s in scores]


def average_available(scores: Sequence[float | None]) -> float | None:
    """Media de los scores disponibles. None si no hay ninguno.

    Promediar solo lo disponible, en vez de imputar en este nivel, mantiene la
    distinción entre "métrica ausente" y "métrica mediocre".
    """
    present = [s for s in scores if s is not None]
    if not present:
        return None
    return sum(present) / len(present)


def diversification_score(sector_weight: float, threshold: float = 0.30) -> float:
    """Aporte a la diversificación de añadir este sector, en [0, 100].

    Lineal a trozos con un QUIEBRE real en el umbral:

        w=0      -> 100   (sector ausente: máxima aportación)
        w=θ      ->  90
        w=1      ->   0   (cartera entera en ese sector)

    Las pendientes son -10/θ antes del umbral y -90/(1-θ) después: con θ=0.30
    eso es -33.3 frente a -128.6, es decir 3.9x más pronunciada.

    El quiebre es el requisito: una versión anterior de esta función descendía
    "suavemente" hasta 70 y luego hasta 0, y al desarrollarla resultaba ser
    exactamente 100(1-w) — perfectamente lineal, sin ningún efecto de umbral.
    Penalizar en el 30% exige que las pendientes DIFIERAN.
    """
    weight = max(0.0, min(1.0, sector_weight))
    if weight <= threshold:
        return 100.0 - (10.0 / threshold) * weight
    return 90.0 * (1.0 - (weight - threshold) / (1.0 - threshold))


def grouped_percentile_ranks(
    values: Sequence[float | None],
    groups: Sequence[str | None],
    min_peers: int,
    fallback_groups: Sequence[str | None] | None = None,
) -> list[float | None]:
    """Rango percentil DENTRO de cada grupo (p. ej. el sector), con respaldo.

    Un múltiplo significa cosas distintas según el sector: un P/E de 10 es
    caro en una eléctrica y barato en una tecnológica. Ordenar todo contra
    todo premia al sector barato en lugar de a la empresa barata.

    Reglas, todas deliberadas:

    - Un grupo con MENOS de `min_peers` valores reales NO se ordena por dentro:
      con 3 empresas, «la más barata de su sector» saldría con 83 puntos por no
      tener rivales. Ese grupo cae al respaldo.
    - Un valor sin grupo (`None`) cae también al respaldo.
    - Un valor ausente sigue siendo `None`; aquí no se imputa nada.

    EL RESPALDO. Sin `fallback_groups` es el universo entero, que era el
    comportamiento original. Con él, cada valor cae en el rango de SU grupo de
    respaldo -la clase de activo- en lugar de mezclarse con todo. Es lo que
    impide que el P/E de un fondo de acciones se ordene contra el de una
    empresa: son dos cosas distintas con el mismo nombre.

    Así la escala es siempre la misma (0-100, 50 = la mediana de la referencia
    usada). Quien llama es quien sabe cuál fue la referencia de cada valor y
    debe decirlo en la interfaz: dos scores con la misma escala pero distinta
    referencia no son directamente comparables.
    """
    if fallback_groups is None:
        result = list(percentile_ranks(values))
    else:
        result: list[float | None] = [None] * len(values)
        base: dict[str | None, list[int]] = defaultdict(list)
        for index, group in enumerate(fallback_groups):
            if values[index] is not None:
                base[group].append(index)
        for indices in base.values():
            ranks = percentile_ranks([values[i] for i in indices])
            for index, rank in zip(indices, ranks, strict=True):
                result[index] = rank

    members: dict[str, list[int]] = defaultdict(list)
    for index, group in enumerate(groups):
        if group is not None and values[index] is not None:
            members[group].append(index)

    for indices in members.values():
        if len(indices) < min_peers:
            continue
        ranks = percentile_ranks([values[i] for i in indices])
        for index, rank in zip(indices, ranks, strict=True):
            result[index] = rank
    return result


def group_medians(
    values: Sequence[float | None],
    groups: Sequence[str | None],
    min_peers: int,
) -> dict[str, tuple[float, int]]:
    """Mediana y nº de pares por grupo, solo para los que llegan a `min_peers`.

    La mediana y no la media: un P/E de 300 en un sector de 40 empresas
    arrastra la media y deja de describir a la empresa típica.
    """
    members: dict[str, list[float]] = defaultdict(list)
    for value, group in zip(values, groups, strict=True):
        if group is not None and value is not None:
            members[group].append(value)
    return {
        group: (float(median(items)), len(items))
        for group, items in members.items()
        if len(items) >= min_peers
    }
