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

from app.core.money import ZERO, safe_divide, to_decimal
from app.models import Asset, Portfolio
from app.repositories import market as market_repo
from app.repositories import portfolio as portfolio_repo
from app.schemas.position import PortfolioSummary, PositionRead
from app.services import exposure as exposure_service
from app.services.metrics import percent_change
from app.services.pnl import LedgerReplay, replay_ledger

logger = logging.getLogger(__name__)

HUNDRED = Decimal("100")


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    ratio = safe_divide(numerator, denominator)
    return None if ratio is None else ratio * HUNDRED


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
    rows: list[tuple[PositionRead, Decimal | None]] = []
    without_price: list[str] = []

    for position in open_positions:
        asset = assets.get(position.asset_id)
        if asset is None:  # pragma: no cover - protegido por FK RESTRICT
            logger.error("Activo %s ausente del catálogo", position.asset_id)
            continue

        quote = quotes.get(asset.id)
        fx_now = market_repo.get_fx_rate(db, asset.currency.upper(), base_currency)

        current_price = to_decimal(quote.price) if quote else None
        market_value: Decimal | None = None
        unrealized: Decimal | None = None

        # Hacen falta AMBOS: precio y tipo de cambio. Sin FX no se puede
        # expresar el valor en divisa base, y asumir paridad COP/USD
        # equivocaría la cifra por un factor de ~4000.
        if current_price is not None and fx_now is not None:
            market_value = position.quantity * current_price * fx_now
            unrealized = market_value - position.total_cost
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
