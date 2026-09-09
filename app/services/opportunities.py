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
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import InsufficientUniverse
from app.models import Asset, Portfolio
from app.repositories import market as market_repo
from app.schemas.opportunity import (
    AbsoluteAssessment,
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
    # Métricas de calidad: solo alimentan la calificación absoluta, no el score
    # relativo. Un ETF no las tiene y eso no lo hace peor candidato.
    roe: float | None = None
    profit_margin: float | None = None
    debt_to_equity: float | None = None
    revenue_growth: float | None = None
    notes: list[str] = field(default_factory=list)


def _positive_or_none(value: float | None) -> float | None:
    """Un múltiplo no positivo no es 'barato': es una empresa en pérdidas.

    Dejarlo entrar como valor muy bajo la premiaría por perder dinero, que es
    exactamente lo contrario de lo que el factor de valoración debe medir.
    """
    return value if value is not None and value > 0 else None


def _collect_candidates(db: Session, assets: list[Asset]) -> list[_Candidate]:
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

        snapshot = fundamentals.get(asset.id)
        if snapshot is not None:
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

    return candidates


def _factor_scores(
    candidates: list[_Candidate],
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Calcula V, M y R para todo el universo. Los rangos son cross-seccionales."""

    # --- Value: todas "menor es mejor", por eso se invierten ---
    value_components = [
        invert(percentile_ranks([c.trailing_pe for c in candidates])),
        invert(percentile_ranks([c.forward_pe for c in candidates])),
        invert(percentile_ranks([c.price_to_book for c in candidates])),
        invert(percentile_ranks([c.ev_to_ebitda for c in candidates])),
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


def _market_pe(db: Session, assets: list[Asset]) -> tuple[str | None, float | None]:
    """P/E del índice de referencia, ancla de la valoración absoluta.

    Se busca entre los candidatos ya cargados para no añadir consultas. Si no
    hay ninguno, la señal de valoración queda SIN datos en vez de inventarse
    una referencia: un ancla equivocada es peor que ninguna.
    """
    by_symbol = {a.symbol: a for a in assets}
    fundamentals = market_repo.get_latest_fundamentals(db, [a.id for a in assets])

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


def _confidence(completeness: float) -> str:
    if completeness >= 0.9:
        return "high"
    if completeness >= 0.6:
        return "medium"
    return "low"


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

    candidates = _collect_candidates(db, assets)
    value, momentum, risk = _factor_scores(candidates)
    benchmark_symbol, benchmark_pe = _market_pe(db, assets)
    if benchmark_pe is None:
        warnings.append(
            "Sin referencia de mercado (SPY o equivalente con P/E): la señal de "
            "valoración absoluta queda sin datos en todas las calificaciones."
        )

    summary = portfolio_service.get_summary(db, portfolio)
    weights_by_sector = portfolio_service.sector_weights(summary.positions)
    if not weights_by_sector:
        warnings.append(
            "La cartera no tiene posiciones valoradas: el factor de diversificación "
            "trata todos los sectores como ausentes"
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

    visible = rows
    if quality_tiers is not None:
        wanted = {g.value for g in quality_tiers}
        visible = [row for row in visible if row.assessment.grade in wanted]
    if regions is not None:
        wanted_regions = {r.value for r in regions}
        visible = [row for row in visible if row.market_region in wanted_regions]

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

    return OpportunityResponse(
        portfolio_id=portfolio.id,
        base_currency=portfolio.base_currency,
        as_of=dt.datetime.now(dt.UTC),
        universe_size=len(rows),
        universe_requested=requested,
        matched_size=len(visible),
        opportunities=visible[:limit],
        formula=FORMULA,
        weights={
            "value": w_value,
            "momentum": w_momentum,
            "diversification": w_diversification,
            "risk": -w_risk,
            "baseline": baseline,
        },
        sector_threshold_pct=threshold * 100,
        benchmark_symbol=benchmark_symbol,
        benchmark_pe=benchmark_pe,
        grade_counts=grade_counts,
        region_counts=region_counts,
        universe_quality_warning=quality_warning,
        warnings=warnings,
        excluded=excluded,
    )
