"""Servicio de portafolio: posiciones, valoración y P&L.

Cruza el replay del ledger (exacto, en Decimal) con los datos de mercado
(estimaciones externas, en float). La conversión ocurre en UN solo punto,
`to_decimal`, que siempre pasa por str.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.money import ZERO, safe_divide, to_decimal
from app.models import Asset, Portfolio
from app.repositories import market as market_repo
from app.repositories import portfolio as portfolio_repo
from app.schemas.position import PortfolioSummary, PositionRead
from app.services import correlation
from app.services import exposure as exposure_service
from app.services.metrics import (
    diversification_index,
    max_drawdown,
    percent_change,
)
from app.services.pnl import LedgerReplay, replay_ledger

logger = logging.getLogger(__name__)

HUNDRED = Decimal("100")


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    ratio = safe_divide(numerator, denominator)
    return None if ratio is None else ratio * HUNDRED


def _attribute(
    position, current_price: Decimal, fx_now: Decimal
) -> tuple[Decimal | None, Decimal | None]:
    """Separa el P&L no realizado en efecto ACTIVO y efecto DIVISA.

    Es la cifra que faltaba en todo el tablero. Con una cartera que compra
    fuera, `unrealized_pnl` mezcla dos cosas que no se parecen en nada: lo que
    hizo la empresa y lo que hizo el cambio. Medido sobre SPY a un año, el
    índice subió un 16,6% en dólares mientras quien mide en pesos perdía un
    5,5%, porque el peso se revaluó un 19%. Sin separarlo es imposible
    distinguir "elegí mal" de "se movió la divisa".

    La descomposición es EXACTA, no una aproximación:

        efecto activo = cantidad · (precio_hoy − coste_medio_local) · fx_hoy
        efecto divisa = cantidad · coste_medio_local · (fx_hoy − fx_medio)

    y su suma es `cantidad · precio_hoy · fx_hoy − coste_total_base`, que es
    exactamente el P&L no realizado. El efecto del activo se mide al tipo de
    HOY -y no al medio- porque es la convención que deja el residuo en cero;
    repartir el término cruzado de otro modo haría que las dos cifras no
    sumaran el total y el desglose dejaría de ser verificable a ojo.

    Devuelve (None, None) si el activo cotiza en la divisa base: ahí no hay
    nada que atribuir, y un cero invitaría a leer "la divisa no aportó" cuando
    lo cierto es que no interviene.
    """
    average_fx = position.average_fx
    if average_fx is None or average_fx == fx_now:
        return None, None

    average_local = position.average_cost_local
    asset_effect = position.quantity * (current_price - average_local) * fx_now
    fx_effect = position.quantity * average_local * (fx_now - average_fx)
    return asset_effect, fx_effect


def build_positions(
    db: Session, portfolio: Portfolio, replay: LedgerReplay
) -> tuple[list[PositionRead], Decimal | None, list[str]]:
    """Construye las posiciones abiertas valoradas a precio actual.

    Devuelve (posiciones, valor_de_mercado_total, símbolos_sin_precio).
    El valor total es None si NINGUNA posición tiene precio: distinguirlo de
    cero es importante, porque un cero implica que la cartera no vale nada.
    """
    open_positions = replay.open_positions
    if not open_positions:
        return [], ZERO, []

    asset_ids = [p.asset_id for p in open_positions]
    assets: dict[int, Asset] = {
        a.id: a for a in db.query(Asset).filter(Asset.id.in_(asset_ids)).all()
    }
    quotes = market_repo.get_quotes(db, asset_ids)

    base_currency = portfolio.base_currency.upper()
    # Los tipos de cambio, UNA vez por divisa y no una por posición. Antes
    # `get_fx_rate` vivía dentro del bucle: veinte posiciones en dólares eran
    # veinte consultas -cuarenta contando el intento con el par inverso-
    # resolviendo el mismo USD->COP. Una cartera tiene decenas de posiciones
    # pero dos o tres divisas.
    fx_by_currency = market_repo.get_fx_rates(
        db, (a.currency for a in assets.values()), base_currency
    )
    rows: list[tuple[PositionRead, Decimal | None]] = []
    without_price: list[str] = []

    for position in open_positions:
        asset = assets.get(position.asset_id)
        if asset is None:  # pragma: no cover - protegido por FK RESTRICT
            logger.error("Activo %s ausente del catálogo", position.asset_id)
            continue

        quote = quotes.get(asset.id)
        fx_now = fx_by_currency.get(asset.currency.upper())

        current_price = to_decimal(quote.price) if quote else None
        market_value: Decimal | None = None
        unrealized: Decimal | None = None
        asset_pnl: Decimal | None = None
        fx_pnl: Decimal | None = None

        # Hacen falta AMBOS: precio y tipo de cambio. Sin FX no se puede
        # expresar el valor en divisa base, y asumir paridad COP/USD
        # equivocaría la cifra por un factor de ~4000.
        if current_price is not None and fx_now is not None:
            market_value = position.quantity * current_price * fx_now
            unrealized = market_value - position.total_cost
            asset_pnl, fx_pnl = _attribute(position, current_price, fx_now)
        else:
            without_price.append(asset.symbol)

        rows.append(
            (
                PositionRead(
                    symbol=asset.symbol,
                    name=asset.name,
                    asset_type=asset.asset_type,
                    currency=asset.currency,
                    sector=asset.sector,
                    exposure_bucket=exposure_service.exposure_bucket(asset),
                    quantity=position.quantity,
                    average_cost=position.average_cost,
                    cost_basis=position.total_cost,
                    total_invested=position.total_invested,
                    realized_pnl=position.realized_pnl,
                    dividend_income=position.dividend_income,
                    current_price=current_price,
                    current_price_base=(
                        current_price * fx_now
                        if current_price is not None and fx_now is not None
                        else None
                    ),
                    price_as_of=quote.quote_time if quote else None,
                    is_stale=bool(quote.is_stale) if quote else True,
                    fx_rate_to_base=fx_now if fx_now is not None else ZERO,
                    market_value=market_value,
                    unrealized_pnl=unrealized,
                    unrealized_return_pct=(
                        _pct(unrealized, position.total_cost) if unrealized is not None else None
                    ),
                    day_change_pct=(
                        to_decimal(percent_change(quote.price, quote.previous_close))
                        if quote
                        else None
                    ),
                    weight_pct=None,  # se rellena abajo, cuando se conoce el total
                    asset_pnl=asset_pnl,
                    fx_pnl=fx_pnl,
                    average_cost_local=(
                        position.average_cost_local
                        if asset.currency.upper() != base_currency
                        else None
                    ),
                    average_fx_rate=(
                        position.average_fx
                        if asset.currency.upper() != base_currency
                        else None
                    ),
                ),
                market_value,
            )
        )

    priced = [value for _, value in rows if value is not None]
    total_market_value = sum(priced, ZERO) if priced else None

    if total_market_value and total_market_value > ZERO:
        for position_read, value in rows:
            if value is not None:
                position_read.weight_pct = _pct(value, total_market_value)

    positions = [row for row, _ in rows]
    positions.sort(key=lambda p: (p.market_value or ZERO), reverse=True)
    return positions, total_market_value, without_price


def _risk_profile(
    db: Session, positions: list[PositionRead]
) -> tuple[float | None, float | None]:
    """Volatilidad anualizada y máxima caída de LA CARTERA, no de sus partes.

    No es el promedio ponderado de las volatilidades individuales: eso ignora
    la correlación y siempre sale más alto que la realidad, porque supone que
    todo se mueve a la vez. `correlation.portfolio_returns` compone la serie de
    la cartera con los pesos actuales y de ahí sale la cifra.

    El código ya existía y solo lo usaba el motor de sugerencia, así que el
    tablero principal no enseñaba ninguna medida de riesgo propia mientras el
    simulador sí opinaba sobre si una compra lo empeoraba.
    """
    priced = [p for p in positions if p.market_value is not None and p.market_value > ZERO]
    if len(priced) < 1:
        return None, None

    # Se resuelven los ids en UNA consulta por símbolo en vez de recibir el
    # diccionario de `build_positions`: mantiene la firma pequeña y cuesta lo
    # mismo, porque la consulta es por clave única.
    by_symbol = market_repo.get_assets_by_symbols(db, [p.symbol for p in priced])
    weights = {
        by_symbol[p.symbol].id: float(p.market_value)
        for p in priced
        if p.symbol in by_symbol
    }
    if not weights:
        return None, None

    series = market_repo.get_price_series_dated(
        db,
        list(weights),
        since=dt.date.today() - dt.timedelta(days=settings.price_history_days),
        adjusted=True,  # RENDIMIENTO: aquí sí hay que ajustar por splits
    )
    returns = {
        asset_id: correlation.to_returns([price for _, price in serie])
        for asset_id, serie in series.items()
    }
    combined = correlation.portfolio_returns(weights, returns)
    if len(combined) < 2:
        return None, None

    volatility = correlation.annualized(correlation.stdev(combined)) * 100

    # El drawdown necesita una serie de VALOR, no de retornos: se reconstruye
    # componiendo los retornos sobre una base arbitraria, que no afecta al
    # resultado porque el drawdown es una razón.
    level = 100.0
    curve = [level]
    for value in combined:
        level *= 1 + value
        curve.append(level)
    drawdown = max_drawdown(curve)

    return volatility, None if drawdown is None else drawdown * 100


def get_summary(db: Session, portfolio: Portfolio) -> PortfolioSummary:
    """Resumen completo del portafolio, todo en divisa base."""
    transactions = portfolio_repo.get_transactions(db, portfolio.id)
    replay = replay_ledger(transactions, strict=False)

    positions, market_value, without_price = build_positions(db, portfolio, replay)

    realized = replay.realized_pnl
    dividends = replay.dividend_income
    total_cost = replay.total_cost

    unrealized: Decimal | None = None
    if market_value is not None and not without_price:
        unrealized = market_value - total_cost
    elif market_value is not None:
        # Valoración parcial: se suma solo el no realizado de lo que tiene
        # precio, en vez de restar un coste total que incluye posiciones sin
        # valorar (lo que fabricaría una pérdida inexistente).
        unrealized = sum(
            (p.unrealized_pnl for p in positions if p.unrealized_pnl is not None), ZERO
        )

    total_pnl = None if unrealized is None else realized + unrealized + dividends

    # Denominador del retorno: capital TOTAL desplegado, incluido el de las
    # posiciones ya cerradas. Usar solo el coste vivo inflaría el retorno de
    # una cartera que ya vendió la mayor parte.
    total_invested = sum(
        (p.total_invested for p in replay.positions.values()), ZERO
    )

    # Atribución agregada. Solo suma las posiciones donde se pudo calcular,
    # igual que el no realizado parcial: mezclar una posición sin tipo de
    # cambio fabricaría un efecto divisa inexistente.
    attributed = [p for p in positions if p.asset_pnl is not None]
    asset_pnl = sum((p.asset_pnl for p in attributed), ZERO) if attributed else None
    fx_pnl = sum((p.fx_pnl for p in attributed), ZERO) if attributed else None

    bucket_weights = sector_weights(positions)
    diversification = diversification_index(list(bucket_weights.values()))
    top_position = max(
        (p.weight_pct for p in positions if p.weight_pct is not None), default=None
    )
    volatility, drawdown = _risk_profile(db, positions)

    return PortfolioSummary(
        portfolio_id=portfolio.id,
        name=portfolio.name,
        base_currency=portfolio.base_currency,
        as_of=dt.datetime.now(dt.UTC),
        total_cost=total_cost,
        market_value=market_value,
        cash_balance=replay.cash_balance,
        total_value=None if market_value is None else market_value + replay.cash_balance,
        net_invested=replay.net_invested,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        dividend_income=dividends,
        total_pnl=total_pnl,
        total_return_pct=(
            None if total_pnl is None else _pct(total_pnl, total_invested)
        ),
        positions=positions,
        positions_without_price=without_price,
        has_stale_prices=any(p.is_stale for p in positions),
        asset_pnl=asset_pnl,
        fx_pnl=fx_pnl,
        diversification_index=diversification,
        top_position_pct=top_position,
        annualized_volatility_pct=volatility,
        max_drawdown_pct=drawdown,
    )


def get_portfolio_assets(db: Session, portfolio: Portfolio) -> list[Asset]:
    """Activos con posición abierta. Es lo que hay que refrescar al leer."""
    transactions = portfolio_repo.get_transactions(db, portfolio.id)
    replay = replay_ledger(transactions, strict=False)
    asset_ids = [p.asset_id for p in replay.open_positions]
    if not asset_ids:
        return []
    return db.query(Asset).filter(Asset.id.in_(asset_ids)).all()


def sector_weights(positions: list[PositionRead]) -> dict[str, Decimal]:
    """Peso de cada CUBO DE EXPOSICIÓN sobre el valor de mercado, en [0, 1].

    Agrupa por cubo y no por sector GICS: un ETF de índice no tiene sector y
    antes caía en "Desconocido" junto con el oro y los futuros, mezclando cosas
    que diversifican de forma muy distinta.

    Alimenta el factor de diversificación del motor de oportunidades. Solo
    entran posiciones con precio: incluir las no valoradas con peso cero
    distorsionaría los porcentajes de las demás.
    """
    priced = [p for p in positions if p.market_value is not None]
    total = sum((p.market_value for p in priced), ZERO)
    if total <= ZERO:
        return {}

    weights: dict[str, Decimal] = {}
    for position in priced:
        bucket = position.exposure_bucket or "Desconocido"
        weights[bucket] = weights.get(bucket, ZERO) + position.market_value
    return {sector: value / total for sector, value in weights.items()}
