"""Motor de oportunidades: implementación de la fórmula de scoring.

    Score = 0.35*V + 0.30*M + 0.15*D - 0.20*R + 20

con V, M, D, R en [0, 100]. El +20 no es una constante de ajuste: es
exactamente 0.20*100, el desplazamiento que convierte el rango natural
[-20, 80] en [0, 100] mediante una transformación afín, preservando el orden y
las distancias relativas. Un clipping habría distorsionado los extremos.

Todos los factores se normalizan con RANGOS PERCENTILES cross-seccionales
dentro del universo de candidatos, no con umbrales absolutos: un P/E de 15
significa cosas distintas en tecnología y en utilities.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import InsufficientUniverse
from app.models import (
    Asset,
    AssetQuote,
    AssetType,
    DataSyncState,
    FundamentalSnapshot,
    Portfolio,
    PriceHistory,
    Transaction,
)
from app.repositories import market as market_repo
from app.schemas.opportunity import (
    AbsoluteAssessment,
    DataFreshness,
    FactorDetail,
    OpportunityRead,
    OpportunityResponse,
    SignalDetail,
)
from app.services import data_quality, grading
from app.services import exposure as exposure_service
from app.services import portfolio as portfolio_service
from app.services import regions as region_service
from app.services.grading import Grade
from app.services.metrics import (
    annualized_volatility,
    max_drawdown,
    momentum_12_1,
    sma_trend,
)
from app.services.regions import MarketRegion
from app.services.scoring import (
    NEUTRAL_SCORE,
    average_available,
    diversification_score,
    group_medians,
    grouped_percentile_ranks,
    invert,
    percentile_ranks,
)

logger = logging.getLogger(__name__)

FORMULA = "Score = 0.35*Value + 0.30*Momentum + 0.15*Diversification - 0.20*Risk + 20"

# Un candidato al que le falten 2 de los 3 factores INFORMATIVOS (V, M, R) se
# excluye. Sin esta regla, un activo sin ningún dato puntuaría 57.5 -por encima
# de la media- precisamente por no saber nada de él.
MAX_MISSING_FACTORS = 1

# Referencia de mercado para la valoración ABSOLUTA. Se compara contra el P/E
# del índice, no contra los otros candidatos: es lo que permite decir "el mejor
# de estos sigue siendo caro".
BENCHMARK_CANDIDATES = ("SPY", "VTI", "VOO", "IVV")


@dataclass
class _Candidate:
    asset: Asset
    trailing_pe: float | None = None
    forward_pe: float | None = None
    price_to_book: float | None = None
    ev_to_ebitda: float | None = None
    trend: float | None = None
    momentum: float | None = None
    volatility: float | None = None
    drawdown: float | None = None
    beta: float | None = None
    current_price: float | None = None
    price_time: dt.datetime | None = None
    fundamentals_as_of: dt.date | None = None
    # Métricas de calidad: solo alimentan la calificación absoluta, no el score
    # relativo. Un ETF no las tiene y eso no lo hace peor candidato.
    roe: float | None = None
    profit_margin: float | None = None
    debt_to_equity: float | None = None
    revenue_growth: float | None = None
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _ScoredUniverse:
    """El universo ya puntuado, ordenado y con el rango asignado.

    Es deliberadamente INMUTABLE (frozen, `rows` es una tupla) porque se
    comparte entre peticiones desde la caché: si un consumidor reordenara o
    renumerara estas filas, la siguiente petición vería el destrozo.
    """

    rows: tuple[OpportunityRead, ...]
    universe_requested: int
    benchmark_symbol: str | None
    benchmark_pe: float | None
    grade_counts: dict[str, int]
    region_counts: dict[str, int]
    quality_warning: str | None
    freshness: DataFreshness
    warnings: tuple[str, ...]
    excluded: dict[str, str]
    weights: dict[str, float]
    sector_threshold_pct: float


# Tipos de activo que se comparan POR SECTOR. Un ETF sectorial trae sector y
# aun así no es una empresa: su P/E es el de su cesta, y mezclarlo con las
# empresas del sector movería la mediana con un número que no es de nadie.
_SECTOR_COMPARABLE_TYPES = frozenset({AssetType.STOCK, AssetType.ADR, AssetType.REIT})


def sector_group(asset: Asset) -> str | None:
    """Sector contra el que se compara el múltiplo de esta empresa, o None."""
    if asset.sector and asset.asset_type in _SECTOR_COMPARABLE_TYPES:
        return asset.sector
    return None


def _positive_or_none(value: float | None) -> float | None:
    """Un múltiplo no positivo no es 'barato': es una empresa en pérdidas.

    Dejarlo entrar como valor muy bajo la premiaría por perder dinero, que es
    exactamente lo contrario de lo que el factor de valoración debe medir.
    """
    return value if value is not None and value > 0 else None


def _collect_candidates(
    db: Session, assets: list[Asset]
) -> tuple[list[_Candidate], dict[int, object]]:
    """Reúne los datos crudos de cada candidato.

    Devuelve también el diccionario de fundamentales porque `_market_pe` lo
    necesita entero y pedirlo dos veces costaba 12,8 ms por petición (un 7%
    del total) para traer exactamente las mismas filas.
    """
    asset_ids = [a.id for a in assets]
    fundamentals = market_repo.get_latest_fundamentals(db, asset_ids)
    quotes = market_repo.get_quotes(db, asset_ids)
    # UNA consulta para todas las series, con tuplas en vez de objetos ORM.
    # Antes era una consulta por activo: con 120 candidatos eran 120 consultas
    # y 48.000 objetos instanciados solo para leerles un atributo (132 ms
    # frente a 33 ms medidos).
    series_by_asset = market_repo.get_price_series_bulk(
        db, asset_ids, days=settings.price_history_days
    )

    # Fecha de la última barra de cada activo. Es lo que permite distinguir
    # "no se pudo calcular" de "se calculó sobre datos viejos", que para el
    # usuario son cosas muy distintas y hasta ahora se veían igual.
    last_bar = market_repo.get_last_bar_dates(db, asset_ids)
    today = dt.date.today()
    max_age = dt.timedelta(days=settings.price_series_max_age_days)

    candidates: list[_Candidate] = []
    for asset in assets:
        candidate = _Candidate(asset=asset)

        quote = quotes.get(asset.id)
        candidate.current_price = quote.price if quote else None
        candidate.price_time = (quote.quote_time or quote.fetched_at) if quote else None

        snapshot = fundamentals.get(asset.id)
        if snapshot is not None:
            candidate.fundamentals_as_of = snapshot.as_of
            candidate.trailing_pe = _positive_or_none(snapshot.trailing_pe)
            candidate.forward_pe = _positive_or_none(snapshot.forward_pe)
            candidate.price_to_book = _positive_or_none(snapshot.price_to_book)
            candidate.ev_to_ebitda = _positive_or_none(snapshot.ev_to_ebitda)
            candidate.beta = snapshot.beta
            candidate.roe = snapshot.return_on_equity
            candidate.profit_margin = snapshot.profit_margin
            candidate.debt_to_equity = snapshot.debt_to_equity
            candidate.revenue_growth = snapshot.revenue_growth

            # El P/E se rehace con el precio de AHORA. Es precio entre
            # beneficio: se mueve a diario aunque el beneficio sea trimestral,
            # y el guardado puede tener hasta 24 h. No cuesta ninguna llamada
            # porque el beneficio por acción ya está en el snapshot.
            recomputed = data_quality.fresh_trailing_pe(
                candidate.current_price, snapshot.eps_trailing
            )
            if recomputed is not None:
                candidate.trailing_pe = recomputed
        else:
            candidate.notes.append("Sin datos fundamentales")

        # La serie ya viene con adj_close preferido sobre close: sin ajustar
        # por splits, un 2:1 aparece como una caída del 50% y el momentum queda
        # con el signo invertido.
        prices = series_by_asset.get(asset.id, [])
        latest = last_bar.get(asset.id)
        series_is_stale = latest is not None and (today - latest) > max_age

        if series_is_stale:
            # NO se calculan momentum ni riesgo sobre una serie parada.
            #
            # El fallo que esto corrige es silencioso y traicionero: una serie
            # vieja calcula sus indicadores perfectamente, así que el activo
            # salía con `data_completeness` 1.00 y `confidence` alta. GXG llegó
            # al puesto 8 con la última barra de hacía seis semanas, y nada en
            # la pantalla lo delataba. Antes se decía "alta confianza" sobre un
            # precio de hace mes y medio.
            candidate.notes.append(
                f"Histórico detenido el {latest.isoformat()} "
                f"({(today - latest).days} días): no se puntúan tendencia ni riesgo"
            )
        elif len(prices) >= 2:
            candidate.trend = sma_trend(prices)
            candidate.momentum = momentum_12_1(prices)
            candidate.volatility = annualized_volatility(prices)
            candidate.drawdown = max_drawdown(prices)
        else:
            candidate.notes.append("Sin histórico de precios suficiente")

        candidates.append(candidate)

    return candidates, fundamentals


def _factor_scores(
    candidates: list[_Candidate],
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Calcula V, M y R para todo el universo. Los rangos son cross-seccionales.

    VALORACIÓN RELATIVA AL SECTOR. Los cuatro múltiplos se ordenan DENTRO del
    sector de cada empresa cuando éste tiene pares suficientes
    (`opportunity_sector_min_peers`), y contra el universo entero cuando no. Así
    50 significa «en la mediana de mi sector», y un banco no sale barato solo
    por ser un banco. Momentum y riesgo NO se ajustan por sector: lo que sube
    o se mueve mucho lo hace igual para quien lo compra, sea del sector que sea.
    """

    def value_ranks(values: list[float | None]) -> list[float | None]:
        if not settings.opportunity_sector_neutral_value:
            return percentile_ranks(values)
        return grouped_percentile_ranks(
            values,
            [sector_group(c.asset) for c in candidates],
            settings.opportunity_sector_min_peers,
        )

    # --- Value: todas "menor es mejor", por eso se invierten ---
    value_components = [
        invert(value_ranks([c.trailing_pe for c in candidates])),
        invert(value_ranks([c.forward_pe for c in candidates])),
        invert(value_ranks([c.price_to_book for c in candidates])),
        invert(value_ranks([c.ev_to_ebitda for c in candidates])),
    ]
    value = [
        average_available([component[i] for component in value_components])
        for i in range(len(candidates))
    ]

    # --- Momentum: "mayor es mejor", rango directo ---
    momentum_components = [
        percentile_ranks([c.trend for c in candidates]),
        percentile_ranks([c.momentum for c in candidates]),
    ]
    momentum = [
        average_available([component[i] for component in momentum_components])
        for i in range(len(candidates))
    ]

    # --- Risk: "mayor es peor". Se resta, así que NO se invierte ---
    risk_components = [
        percentile_ranks([c.volatility for c in candidates]),
        percentile_ranks([c.drawdown for c in candidates]),
        percentile_ranks([c.beta for c in candidates]),
    ]
    risk = [
        average_available([component[i] for component in risk_components])
        for i in range(len(candidates))
    ]

    return value, momentum, risk


def _market_pe(
    assets: list[Asset], fundamentals: dict[int, object]
) -> tuple[str | None, float | None]:
    """P/E del índice de referencia, ancla de la valoración absoluta.

    Recibe los fundamentales YA CARGADOS en lugar de volver a pedirlos. El
    docstring anterior prometía "no añadir consultas" y hacía justo lo
    contrario: repetía la consulta de todo el universo para leer una sola
    fila. Medido: 12,8 ms por petición, un 7% del total.

    Si no hay ningún índice entre los candidatos, la señal de valoración queda
    SIN datos en vez de inventarse una referencia: un ancla equivocada es peor
    que ninguna.
    """
    by_symbol = {a.symbol: a for a in assets}

    for symbol in BENCHMARK_CANDIDATES:
        asset = by_symbol.get(symbol)
        if asset is None:
            continue
        snapshot = fundamentals.get(asset.id)
        if snapshot and snapshot.trailing_pe and snapshot.trailing_pe > 0:
            return symbol, snapshot.trailing_pe
    return None, None


def _to_signal_detail(signal) -> SignalDetail:
    return SignalDetail(
        name=signal.name, label=signal.label, points=signal.points,
        detail=signal.detail, inputs=signal.inputs,
    )


def _freshness(rows: list[OpportunityRead]) -> DataFreshness:
    prices = [r.price_as_of for r in rows if r.price_as_of is not None]
    funds = [r.fundamentals_as_of for r in rows if r.fundamentals_as_of is not None]
    return DataFreshness(
        prices_newest=max(prices, default=None),
        prices_oldest=min(prices, default=None),
        fundamentals_newest=max(funds, default=None),
        fundamentals_oldest=min(funds, default=None),
    )


def _confidence(completeness: float) -> str:
    if completeness >= 0.9:
        return "high"
    if completeness >= 0.6:
        return "medium"
    return "low"


def _score_universe(
    db: Session, portfolio: Portfolio, assets: list[Asset]
) -> _ScoredUniverse:
    """Puntúa el universo COMPLETO. Es la parte cara, y la única cacheable.

    Todo lo que hay aquí depende solo de (universo, datos de mercado, ledger):
    nada depende del filtro ni del límite, que son parámetros de vista. Esa
    separación es lo que permite memoizar el resultado -ver `_UNIVERSE_CACHE`-
    sin tocar ninguna de las reglas del motor.
    """
    requested = len(assets)
    warnings: list[str] = []
    excluded: dict[str, str] = {}

    if requested < settings.opportunity_min_universe:
        raise InsufficientUniverse(
            f"Se necesitan al menos {settings.opportunity_min_universe} candidatos para "
            f"un ranking con sentido; hay {requested}. Los rangos percentiles sobre un "
            f"universo diminuto son ruido: con n=2 los scores son 25 y 75 sin importar "
            f"los valores. Añade símbolos con ?symbols=NVDA,KO"
        )

    candidates, fundamentals = _collect_candidates(db, assets)
    value, momentum, risk = _factor_scores(candidates)
    benchmark_symbol, benchmark_pe = _market_pe(assets, fundamentals)
    if benchmark_pe is None:
        warnings.append(
            "Sin referencia de mercado (SPY o equivalente con P/E): la señal de "
            "valoración absoluta solo se calcula para las empresas cuyo sector "
            "tiene pares suficientes; el resto queda sin dato de valoración."
        )

    summary = portfolio_service.get_summary(db, portfolio)
    weights_by_sector = portfolio_service.sector_weights(summary.positions)
    if not weights_by_sector:
        warnings.append(
            "La cartera no tiene posiciones valoradas: el factor de diversificación "
            "trata todos los sectores como ausentes"
        )

    # Mediana de P/E por sector, para anclar la valoración ABSOLUTA. Se calcula
    # con el universo completo y ANTES de filtrar: es la referencia de una
    # empresa y no debe cambiar porque el usuario oculte a sus pares.
    sector_groups = [sector_group(c.asset) for c in candidates]
    min_peers = settings.opportunity_sector_min_peers
    sector_trailing = (
        group_medians([c.trailing_pe for c in candidates], sector_groups, min_peers)
        if settings.opportunity_sector_neutral_value
        else {}
    )
    sector_forward = (
        group_medians([c.forward_pe for c in candidates], sector_groups, min_peers)
        if settings.opportunity_sector_neutral_value
        else {}
    )

    w_value = settings.opportunity_weight_value
    w_momentum = settings.opportunity_weight_momentum
    w_diversification = settings.opportunity_weight_diversification
    w_risk = settings.opportunity_weight_risk
    baseline = w_risk * 100.0
    threshold = settings.opportunity_sector_threshold

    rows: list[OpportunityRead] = []

    for index, candidate in enumerate(candidates):
        asset = candidate.asset
        raw = (value[index], momentum[index], risk[index])
        missing = sum(1 for factor in raw if factor is None)

        if missing > MAX_MISSING_FACTORS:
            excluded[asset.symbol] = (
                f"Faltan {missing} de 3 factores informativos (valoración, momentum, riesgo)"
            )
            continue

        # Cubo de exposición, no sector GICS a secas. Un ETF amplio o el oro
        # diversifican de verdad, y con sectores puros quedaban como
        # "desconocido" y recibían el valor neutro: el sistema desaconsejaba
        # justo los instrumentos más diversificadores.
        bucket = exposure_service.exposure_bucket(asset)
        bucket_assumed = exposure_service.is_assumed_bucket(asset)
        sector_weight = float(weights_by_sector.get(bucket, Decimal("0")))

        if bucket == exposure_service.UNKNOWN:
            diversification = NEUTRAL_SCORE
            diversification_available = False
            candidate.notes.append("Exposición desconocida: diversificación neutra")
        else:
            diversification = diversification_score(sector_weight, threshold)
            diversification_available = True
            if bucket_assumed:
                candidate.notes.append(
                    f"Se asume exposición «{bucket}» por ser un fondo sin sector declarado"
                )
            if sector_weight > threshold:
                candidate.notes.append(
                    f"«{bucket}» ya pesa {sector_weight * 100:.1f}% "
                    f"(umbral {threshold * 100:.0f}%): penalización activa"
                )

        # Imputación neutra: con rangos percentiles, 50 ES el valor neutro por
        # construcción. Es la única imputación defendible aquí; poner 0
        # situaría al candidato en el peor lugar por no tener datos.
        value_score = value[index] if value[index] is not None else NEUTRAL_SCORE
        momentum_score = momentum[index] if momentum[index] is not None else NEUTRAL_SCORE
        risk_score = risk[index] if risk[index] is not None else NEUTRAL_SCORE

        score = (
            w_value * value_score
            + w_momentum * momentum_score
            + w_diversification * diversification
            - w_risk * risk_score
            + baseline
        )
        # El clamp solo absorbe error de coma flotante; la fórmula ya está
        # acotada por construcción.
        score = max(0.0, min(100.0, score))

        group = sector_groups[index]
        sector_stat = sector_trailing.get(group) if group else None
        forward_stat = sector_forward.get(group) if group else None

        assessment = grading.assess(
            trailing_pe=candidate.trailing_pe,
            forward_pe=candidate.forward_pe,
            price_to_book=candidate.price_to_book,
            market_pe=benchmark_pe,
            sma_trend=candidate.trend,
            momentum_12_1=candidate.momentum,
            volatility=candidate.volatility,
            max_drawdown=candidate.drawdown,
            roe=candidate.roe,
            profit_margin=candidate.profit_margin,
            debt_to_equity=candidate.debt_to_equity,
            revenue_growth=candidate.revenue_growth,
            sector_trailing_pe=sector_stat[0] if sector_stat else None,
            sector_forward_pe=forward_stat[0] if forward_stat else None,
            sector_name=group,
        )

        completeness = (3 - missing) / 3
        rows.append(
            OpportunityRead(
                rank=0,  # se asigna tras ordenar
                symbol=asset.symbol,
                name=asset.name,
                sector=asset.sector,
                currency=asset.currency,
                market_region=region_service.classify(asset),
                score=round(score, 2),
                baseline=baseline,
                value=FactorDetail(
                    score=round(value_score, 2),
                    weight=w_value,
                    contribution=round(w_value * value_score, 2),
                    available=value[index] is not None,
                    inputs={
                        "trailing_pe": candidate.trailing_pe,
                        "forward_pe": candidate.forward_pe,
                        "price_to_book": candidate.price_to_book,
                        "ev_to_ebitda": candidate.ev_to_ebitda,
                    },
                ),
                momentum=FactorDetail(
                    score=round(momentum_score, 2),
                    weight=w_momentum,
                    contribution=round(w_momentum * momentum_score, 2),
                    available=momentum[index] is not None,
                    inputs={
                        "sma50_over_sma200_minus_1": candidate.trend,
                        "momentum_12_1": candidate.momentum,
                    },
                ),
                diversification=FactorDetail(
                    score=round(diversification, 2),
                    weight=w_diversification,
                    contribution=round(w_diversification * diversification, 2),
                    available=diversification_available,
                    inputs={"sector_weight": sector_weight},
                ),
                risk=FactorDetail(
                    score=round(risk_score, 2),
                    weight=-w_risk,
                    contribution=round(-w_risk * risk_score, 2),
                    available=risk[index] is not None,
                    inputs={
                        "annualized_volatility": candidate.volatility,
                        "max_drawdown": candidate.drawdown,
                        "beta": candidate.beta,
                    },
                ),
                current_price=candidate.current_price,
                price_as_of=candidate.price_time,
                fundamentals_as_of=candidate.fundamentals_as_of,
                value_basis="sector" if sector_stat else "universe",
                value_reference=group if sector_stat else None,
                sector_pe=round(sector_stat[0], 2) if sector_stat else None,
                sector_peer_count=sector_stat[1] if sector_stat else None,
                exposure_bucket=bucket,
                exposure_is_assumed=bucket_assumed,
                sector_weight_pct=round(sector_weight * 100, 2),
                assessment=AbsoluteAssessment(
                    grade=assessment.grade.value,
                    label=assessment.label,
                    points=assessment.points,
                    max_points=assessment.max_points,
                    signals=[_to_signal_detail(x) for x in assessment.signals],
                    notes=assessment.notes,
                ),
                data_completeness=round(completeness, 2),
                confidence=_confidence(completeness),
                notes=candidate.notes,
            )
        )

    if len(rows) < settings.opportunity_min_universe:
        raise InsufficientUniverse(
            f"Solo {len(rows)} de {requested} candidatos tienen datos suficientes "
            f"(mínimo {settings.opportunity_min_universe}). Ejecuta "
            f"POST /api/market/refresh para poblar precios y fundamentales."
        )

    rows.sort(key=lambda row: row.score, reverse=True)
    # El rango se asigna sobre TODAS las filas, no sobre las `limit` mostradas:
    # con filtros activos, la fila que se enseñe debe seguir diciendo qué
    # puesto ocupa en el ranking completo.
    for position, row in enumerate(rows, start=1):
        row.rank = position

    # Recuentos ANTES de filtrar. Son los que alimentan los chips de la
    # interfaz, y tienen que seguir diciendo cuántos hay en total de cada
    # categoría: si reflejaran el filtro activo, al seleccionar "Muy buena"
    # el resto de chips se pondrían a cero y no habría forma de volver.
    grade_counts: dict[str, int] = {}
    region_counts: dict[str, int] = {}
    for row in rows:
        grade_counts[row.assessment.grade] = grade_counts.get(row.assessment.grade, 0) + 1
        region_counts[row.market_region] = region_counts.get(row.market_region, 0) + 1

    # EL aviso que faltaba. El score es ordinal, así que siempre hay un primero;
    # esto dice si ese primero es bueno en términos absolutos o solo el menos
    # malo de un conjunto flojo.
    quality_warning = None
    good_or_better = grade_counts.get("A", 0) + grade_counts.get("B", 0)
    if rows and good_or_better == 0:
        best = rows[0]
        quality_warning = (
            f"Ningún candidato alcanza calificación «Buena». El primero del "
            f"ranking ({best.symbol}) está calificado como «{best.assessment.label}»: "
            f"encabeza esta lista, pero no por ser bueno en términos absolutos."
        )

    return _ScoredUniverse(
        rows=tuple(rows),
        universe_requested=requested,
        benchmark_symbol=benchmark_symbol,
        benchmark_pe=benchmark_pe,
        grade_counts=grade_counts,
        region_counts=region_counts,
        quality_warning=quality_warning,
        freshness=_freshness(rows),
        warnings=tuple(warnings),
        excluded=excluded,
        weights={
            "value": w_value,
            "momentum": w_momentum,
            "diversification": w_diversification,
            "risk": -w_risk,
            "baseline": baseline,
        },
        sector_threshold_pct=threshold * 100,
    )


# ---------------------------------------------------------------------------
# Memoización del universo puntuado
# ---------------------------------------------------------------------------
#
# POR QUÉ. Filtrar por región o por calificación es una operación de VISTA: el
# motor puntúa siempre el universo completo y el filtro solo decide qué filas
# se enseñan (ver el docstring de `compute_opportunities`). Aun así, cada clic
# en un chip repetía el trabajo entero: 186 ms medidos sobre los 494 activos
# reales, de los cuales 85 ms eran releer 135.851 barras de `price_history`
# para recalcular momentum, volatilidad y drawdown IDÉNTICOS. El sondeo del
# refresco -cada 3 s, hasta 40 veces- hacía lo mismo.
#
# Con la caché, un clic de filtro cuesta la huella (~1,2 ms) más el recorte.
#
# QUÉ INVALIDA. La huella NO es "la fecha de la última barra": las cotizaciones
# cambian intradía y sí mueven el score, porque `fresh_trailing_pe` rehace el
# P/E con el precio de ahora. Una caché que ignorase eso congelaría el ranking
# justo durante el refresco de fondo, que es cuando el usuario está mirando.
#
# Se pregunta por agregados baratos, todos medidos:
#
#     data_sync_state  count + max(last_success_at)   0,58 ms
#     assets           count + max(updated_at)        0,50 ms
#     fundamentals     max(as_of)                     0,10 ms
#     price_history    max(date)                      0,03 ms
#     asset_quotes     max(fetched_at)                0,02 ms
#     transactions     count + max(updated_at)        0,01 ms
#
# `count(*)` sobre `price_history` o `fundamental_snapshots` NO entra: son 83 y
# 41 ms, un barrido completo que costaría más que lo que ahorra.
#
# `data_sync_state` es la red principal porque TODA escritura del proveedor
# pasa por `mark_success`, que sella `last_success_at`. Los otros agregados son
# la red secundaria para escrituras que no pasan por ahí (una carga directa,
# un script de backfill). Queda un hueco honesto: rellenar barras ANTIGUAS sin
# tocar `data_sync_state` no movería `max(date)`. Es un caso de mantenimiento
# manual, y la caché muere al reiniciar el proceso.
#
# Los pesos de la fórmula entran en la huella porque son ajustables por
# entorno, y un test que los cambie debe ver el cambio.

_CACHE_MAX_ENTRIES = 4
_UNIVERSE_CACHE: OrderedDict[tuple, _ScoredUniverse] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def clear_cache() -> None:
    """Vacía la memoización. La usan los tests y cualquier reseteo manual."""
    with _CACHE_LOCK:
        _UNIVERSE_CACHE.clear()


def _universe_fingerprint(
    db: Session, portfolio: Portfolio, assets: list[Asset]
) -> tuple:
    """Huella de todo aquello de lo que depende un score.

    Si algo de esto cambia, el ranking puede cambiar y hay que recalcular. Si
    nada cambia, el ranking es bit a bit el mismo y devolverlo es correcto.
    """
    quotes_at = db.scalar(select(func.max(AssetQuote.fetched_at)))
    bars_at = db.scalar(select(func.max(PriceHistory.date)))
    fundamentals_at = db.scalar(select(func.max(FundamentalSnapshot.as_of)))
    sync_state = db.execute(
        select(func.count(DataSyncState.id), func.max(DataSyncState.last_success_at))
    ).one()
    assets_state = db.execute(
        select(func.count(Asset.id), func.max(Asset.updated_at))
    ).one()
    # El ledger entra por el factor de diversificación: los pesos por cubo
    # salen de las posiciones abiertas. `updated_at` cubre las ediciones, que
    # no mueven ni el conteo ni el id máximo.
    ledger = db.execute(
        select(func.count(Transaction.id), func.max(Transaction.updated_at)).where(
            Transaction.portfolio_id == portfolio.id
        )
    ).one()

    return (
        portfolio.id,
        portfolio.base_currency,
        tuple(a.id for a in assets),
        # La antigüedad de la serie se mide contra el día de hoy: al cambiar de
        # día, una serie que ayer era fresca puede dejar de serlo.
        dt.date.today(),
        str(quotes_at),
        str(bars_at),
        str(fundamentals_at),
        sync_state,
        assets_state,
        ledger,
        settings.opportunity_weight_value,
        settings.opportunity_weight_momentum,
        settings.opportunity_weight_diversification,
        settings.opportunity_weight_risk,
        settings.opportunity_sector_threshold,
        settings.opportunity_sector_neutral_value,
        settings.opportunity_sector_min_peers,
        settings.opportunity_min_universe,
        settings.price_series_max_age_days,
        settings.price_history_days,
    )


def _scored_universe(
    db: Session, portfolio: Portfolio, assets: list[Asset]
) -> _ScoredUniverse:
    key = _universe_fingerprint(db, portfolio, assets)

    with _CACHE_LOCK:
        cached = _UNIVERSE_CACHE.get(key)
        if cached is not None:
            _UNIVERSE_CACHE.move_to_end(key)
            return cached

    # Se puntúa FUERA del candado: es lo que tarda, y bloquear aquí serializaría
    # peticiones que no comparten nada. Dos hilos a la vez harían el trabajo dos
    # veces y guardarían el mismo resultado, que es correcto aunque se
    # desperdicie; retener el candado 186 ms sería peor.
    scored = _score_universe(db, portfolio, assets)

    with _CACHE_LOCK:
        _UNIVERSE_CACHE[key] = scored
        _UNIVERSE_CACHE.move_to_end(key)
        while len(_UNIVERSE_CACHE) > _CACHE_MAX_ENTRIES:
            _UNIVERSE_CACHE.popitem(last=False)
    return scored


def compute_opportunities(
    db: Session,
    portfolio: Portfolio,
    *,
    assets: list[Asset],
    limit: int = 10,
    regions: set[MarketRegion] | None = None,
    quality_tiers: set[Grade] | None = None,
) -> OpportunityResponse:
    """Evalúa el universo y devuelve el ranking con desglose por factor.

    LOS FILTROS SE APLICAN DESPUÉS DE PUNTUAR, NUNCA ANTES
    =====================================================
    El score es un RANGO PERCENTIL cross-seccional: el valor de cada activo
    depende de contra quién se le compara. Si se filtrara el universo antes de
    puntuar, pedir "solo Colombia" recalcularía los percentiles entre 19
    activos y un valor mediocre saldría con 90 puntos por no tener rivales.

    Filtrando después, el score que se muestra sigue siendo el del universo
    completo -que es lo que el `disclaimer` promete- y el filtro solo decide
    qué filas se enseñan. Por lo mismo el `rank` es la posición en el ranking
    COMPLETO: ver que el mejor colombiano es el #7 de 494 es información, y
    renumerarlo a #1 la destruiría.

    Esa misma independencia es la que permite memoizar: lo de arriba de esta
    línea no depende de `limit`, `regions` ni `quality_tiers`.
    """
    scored = _scored_universe(db, portfolio, assets)

    visible = list(scored.rows)
    if quality_tiers is not None:
        wanted = {g.value for g in quality_tiers}
        visible = [row for row in visible if row.assessment.grade in wanted]
    if regions is not None:
        wanted_regions = {r.value for r in regions}
        visible = [row for row in visible if row.market_region in wanted_regions]

    return OpportunityResponse(
        portfolio_id=portfolio.id,
        base_currency=portfolio.base_currency,
        as_of=dt.datetime.now(dt.UTC),
        universe_size=len(scored.rows),
        universe_requested=scored.universe_requested,
        matched_size=len(visible),
        opportunities=visible[:limit],
        formula=FORMULA,
        weights=scored.weights,
        sector_threshold_pct=scored.sector_threshold_pct,
        benchmark_symbol=scored.benchmark_symbol,
        benchmark_pe=scored.benchmark_pe,
        grade_counts=scored.grade_counts,
        region_counts=scored.region_counts,
        universe_quality_warning=scored.quality_warning,
        freshness=scored.freshness,
        warnings=list(scored.warnings),
        excluded=scored.excluded,
    )
