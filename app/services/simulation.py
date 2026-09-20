"""Simulador "What-If": impacto de operaciones propuestas, sin persistirlas.

AISLAMIENTO. Es el requisito central y se garantiza por construcción, no por
convención:

1. Las transacciones simuladas se crean como objetos ORM **desconectados**:
   nunca se pasan por `db.add()`, y su relación `portfolio` no se asigna (solo
   `portfolio_id`), porque asignar el objeto las engancharía a la sesión por
   cascada y el siguiente `commit()` de cualquier otra parte las escribiría.
2. Todo el cálculo corre dentro de `db.no_autoflush`: sin él, cualquier consulta
   dispararía un flush que podría arrastrar objetos sucios.
3. Los activos que aún no existen en el catálogo se representan con stubs
   desconectados e id NEGATIVO, imposible de colisionar con una fila real.

El test `test_simulation_writes_nothing` cuenta las filas antes y después.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.exceptions import InvalidLedgerOperation
from app.core.money import ZERO, safe_divide, to_decimal
from app.models import Asset, Portfolio, Transaction, TransactionType
from app.repositories import market as market_repo
from app.repositories import portfolio as portfolio_repo
from app.schemas.simulation import (
    PortfolioSnapshot,
    SectorExposure,
    SimulatedTrade,
    SimulationDeltas,
    SimulationRequest,
    SimulationResponse,
)
from app.services import exposure as exposure_service
from app.services.metrics import diversification_index, herfindahl
from app.services.pnl import LedgerReplay, replay_ledger

logger = logging.getLogger(__name__)

HUNDRED = Decimal("100")


@dataclass
class _Valuation:
    """Resultado de valorar un replay contra los precios actuales."""

    market_value: Decimal
    cost_basis: Decimal
    cash_balance: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    dividend_income: Decimal
    total_invested: Decimal
    by_sector: dict[str, Decimal]
    by_symbol: dict[str, Decimal]
    positions_without_price: list[str]


def _pct(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    ratio = safe_divide(numerator, denominator)
    return None if ratio is None else ratio * HUNDRED


def effective_holdings(weights: list[Decimal]) -> Decimal | None:
    """Número efectivo de posiciones = 1/HHI.

    Más legible que el HHI para presentar: "tu cartera se comporta como si
    tuviera 3,2 posiciones iguales" dice más que "HHI = 0,31".
    """
    hhi = herfindahl(weights)
    if hhi <= ZERO:
        return None
    return Decimal("1") / hhi


def _value_replay(
    db: Session,
    portfolio: Portfolio,
    replay: LedgerReplay,
    assets: dict[int, Asset],
) -> _Valuation:
    """Valora las posiciones abiertas de un replay a precios actuales."""
    base_currency = portfolio.base_currency.upper()
    real_ids = [aid for aid in assets if aid > 0]
    quotes = market_repo.get_quotes(db, real_ids)

    # Mismo deduplicado que en `portfolio.build_positions`, y por la misma
    # razón: el "antes" del simulador y el portafolio real tienen que valorar
    # con los mismos tipos, o la comparación que el simulador promete no
    # significaría nada.
    fx_by_currency = market_repo.get_fx_rates(
        db, (a.currency for a in assets.values()), base_currency
    )

    def fx_for(currency: str) -> Decimal | None:
        key = currency.upper()
        if key not in fx_by_currency:
            # Una divisa que no venía en `assets` se resuelve igualmente: el
            # deduplicado es una optimización, nunca un silencio que devuelva
            # "sin tipo" por no haberlo buscado.
            fx_by_currency[key] = market_repo.get_fx_rate(db, key, base_currency)
        return fx_by_currency[key]

    by_sector: dict[str, Decimal] = {}
    by_symbol: dict[str, Decimal] = {}
    without_price: list[str] = []
    market_value = ZERO
    unrealized = ZERO

    for position in replay.open_positions:
        asset = assets.get(position.asset_id)
        if asset is None:
            continue

        price = _price_for(position.asset_id, quotes, assets)
        rate = fx_for(asset.currency)

        if price is None or rate is None:
            without_price.append(asset.symbol)
            continue

        value = position.quantity * price * rate
        market_value += value
        unrealized += value - position.total_cost

        # Mismo criterio que el motor de oportunidades: cubo de exposición, no
        # sector GICS. Si aquí se agrupara distinto, el "antes" del simulador y
        # el factor de diversificación medirían cosas diferentes.
        bucket = exposure_service.exposure_bucket(asset)
        by_sector[bucket] = by_sector.get(bucket, ZERO) + value
        by_symbol[asset.symbol] = by_symbol.get(asset.symbol, ZERO) + value

    return _Valuation(
        market_value=market_value,
        cost_basis=replay.total_cost,
        cash_balance=replay.cash_balance,
        realized_pnl=replay.realized_pnl,
        unrealized_pnl=unrealized,
        dividend_income=replay.dividend_income,
        total_invested=sum(
            (p.total_invested for p in replay.positions.values()), ZERO
        ),
        by_sector=by_sector,
        by_symbol=by_symbol,
        positions_without_price=without_price,
    )


def _price_for(
    asset_id: int, quotes: dict, assets: dict[int, Asset]
) -> Decimal | None:
    """Precio actual de un activo. Los stubs simulados no tienen cotización."""
    quote = quotes.get(asset_id)
    if quote is None:
        return None
    return to_decimal(quote.price)


def _snapshot(valuation: _Valuation, base_currency: str) -> PortfolioSnapshot:
    total = valuation.market_value
    weights = (
        [value / total for value in valuation.by_sector.values()] if total > ZERO else []
    )
    position_weights = (
        [value / total for value in valuation.by_symbol.values()] if total > ZERO else []
    )

    exposures = [
        SectorExposure(
            sector=sector,
            value=value,
            weight_pct=_pct(value, total) or ZERO,
        )
        for sector, value in sorted(
            valuation.by_sector.items(), key=lambda item: item[1], reverse=True
        )
    ]

    total_pnl = valuation.realized_pnl + valuation.unrealized_pnl + valuation.dividend_income

    return PortfolioSnapshot(
        base_currency=base_currency,
        market_value=valuation.market_value,
        cost_basis=valuation.cost_basis,
        cash_balance=valuation.cash_balance,
        total_value=valuation.market_value + valuation.cash_balance,
        realized_pnl=valuation.realized_pnl,
        unrealized_pnl=valuation.unrealized_pnl,
        dividend_income=valuation.dividend_income,
        total_pnl=total_pnl,
        total_return_pct=_pct(total_pnl, valuation.total_invested),
        position_count=len(valuation.by_symbol),
        sector_exposures=exposures,
        diversification_index=diversification_index(weights),
        effective_sectors=effective_holdings(weights),
        effective_positions=effective_holdings(position_weights),
        largest_position_pct=(
            max((_pct(v, total) or ZERO for v in valuation.by_symbol.values()), default=ZERO)
            if total > ZERO
            else ZERO
        ),
        positions_without_price=valuation.positions_without_price,
    )


def _build_simulated_transactions(
    db: Session,
    portfolio: Portfolio,
    trades: list[SimulatedTrade],
    assets: dict[int, Asset],
    warnings: list[str],
) -> list[Transaction]:
    """Crea transacciones DESCONECTADAS a partir de las operaciones propuestas.

    Ninguna pasa por `db.add()`. La relación `portfolio` se deja sin asignar a
    propósito: asignar el objeto las engancharía a la sesión por cascada.
    """
    base_currency = portfolio.base_currency.upper()
    symbols = [trade.symbol for trade in trades if trade.symbol]
    catalog = market_repo.get_assets_by_symbols(db, symbols) if symbols else {}

    stub_id = -1
    simulated: list[Transaction] = []
    now = dt.datetime.now(dt.UTC)

    for trade in trades:
        asset: Asset | None = None
        if trade.symbol:
            asset = catalog.get(trade.symbol)
            if asset is None:
                # Stub desconectado con id negativo: imposible que colisione
                # con una fila real, e imposible que se persista porque nunca
                # se añade a la sesión.
                asset = Asset(
                    symbol=trade.symbol,
                    currency=trade.currency or "USD",
                    sector=trade.sector,
                )
                asset.id = stub_id
                stub_id -= 1
                if trade.sector is None:
                    warnings.append(
                        f"{trade.symbol} no está en el catálogo y no se indicó sector: "
                        f"se agrupa en «Desconocido» y su efecto en la diversificación "
                        f"es aproximado. Sincroniza el símbolo o envía `sector`."
                    )
            assets[asset.id] = asset

        currency = (trade.currency or (asset.currency if asset else base_currency)).upper()
        fx = trade.fx_rate_to_base
        if fx is None:
            fx = (
                Decimal("1")
                if currency == base_currency
                else market_repo.get_fx_rate(db, currency, base_currency)
            )
        if fx is None:
            raise InvalidLedgerOperation(
                f"No hay tipo de cambio {currency}->{base_currency} para simular "
                f"{trade.symbol or trade.type}. Indica `fx_rate_to_base`."
            )

        simulated.append(
            Transaction(
                portfolio_id=portfolio.id,
                asset_id=asset.id if asset else None,
                type=trade.type,
                executed_at=now,
                quantity=trade.quantity,
                price=trade.price,
                cash_amount=trade.cash_amount,
                fees=trade.fees,
                currency=currency,
                fx_rate_to_base=fx,
            )
        )

    return simulated


def simulate(
    db: Session, portfolio: Portfolio, request: SimulationRequest
) -> SimulationResponse:
    """Calcula el impacto de las operaciones propuestas sin persistir nada."""
    warnings: list[str] = []

    # `no_autoflush` es la segunda barrera: impide que una consulta dispare un
    # flush con objetos sucios en la sesión mientras se simula.
    with db.no_autoflush:
        existing = portfolio_repo.get_transactions(db, portfolio.id)

        assets: dict[int, Asset] = {}
        for transaction in existing:
            if transaction.asset is not None:
                assets[transaction.asset.id] = transaction.asset

        current_replay = replay_ledger(existing, strict=False)
        current = _value_replay(db, portfolio, current_replay, assets)

        simulated_transactions = _build_simulated_transactions(
            db, portfolio, request.trades, assets, warnings
        )

        # strict=True: una venta por encima de lo disponible debe fallar en la
        # simulación igual que fallaría al ejecutarla. Un simulador que permite
        # lo imposible no sirve para decidir.
        simulated_replay = replay_ledger(
            [*existing, *simulated_transactions], strict=True
        )
        simulated = _value_replay(db, portfolio, simulated_replay, assets)

    # Las operaciones sobre símbolos nuevos no tienen cotización, así que su
    # valor de mercado simulado se toma del precio de la propia operación.
    _apply_stub_valuation(request, simulated, portfolio, assets, warnings)

    base_currency = portfolio.base_currency
    current_snapshot = _snapshot(current, base_currency)
    simulated_snapshot = _snapshot(simulated, base_currency)

    # Sin cotizaciones ni tipo de cambio no hay valor de mercado, así que los
    # pesos por sector y el índice de diversificación salen vacíos. La respuesta
    # sigue siendo correcta -el P&L y la caja sí se calculan-, pero callarlo
    # dejaría al usuario mirando un panel de diversificación en blanco sin saber
    # por qué.
    if current_snapshot.market_value == ZERO and current_replay.open_positions:
        warnings.append(
            "No hay cotizaciones ni tipo de cambio en caché: la comparación de "
            "pesos por sector y del índice de diversificación no se puede "
            "calcular. Sincroniza los datos de mercado primero."
        )

    return SimulationResponse(
        portfolio_id=portfolio.id,
        base_currency=base_currency,
        as_of=dt.datetime.now(dt.UTC),
        trades=request.trades,
        current_state=current_snapshot,
        simulated_state=simulated_snapshot,
        deltas=_deltas(current_snapshot, simulated_snapshot),
        warnings=warnings,
    )


def _apply_stub_valuation(
    request: SimulationRequest,
    simulated: _Valuation,
    portfolio: Portfolio,
    assets: dict[int, Asset],
    warnings: list[str],
) -> None:
    """Valora las posiciones simuladas de símbolos sin cotización.

    Un símbolo nuevo no tiene fila en `asset_quotes`, así que la valoración lo
    dejaría fuera y la comparación antes/después sería incoherente: el dinero
    saldría de la caja pero no aparecería como posición. Se usa el precio de la
    operación propuesta, que es la mejor estimación disponible y además es la
    que el usuario acaba de escribir.
    """
    for trade in request.trades:
        if trade.type is not TransactionType.BUY or not trade.symbol:
            continue
        if trade.symbol in simulated.by_symbol:
            continue

        asset = next(
            (a for a in assets.values() if a.symbol == trade.symbol and a.id < 0), None
        )
        if asset is None or trade.price is None or trade.quantity is None:
            continue

        fx = trade.fx_rate_to_base or Decimal("1")
        value = trade.quantity * trade.price * fx
        sector = trade.sector or "Desconocido"

        simulated.by_symbol[trade.symbol] = value
        simulated.by_sector[sector] = simulated.by_sector.get(sector, ZERO) + value
        simulated.market_value += value
        if trade.symbol in simulated.positions_without_price:
            simulated.positions_without_price.remove(trade.symbol)
        warnings.append(
            f"{trade.symbol} se valora al precio propuesto ({trade.price}): "
            f"aún no hay cotización de mercado para ese símbolo."
        )


def _deltas(current: PortfolioSnapshot, simulated: PortfolioSnapshot) -> SimulationDeltas:
    def diff(a: Decimal | None, b: Decimal | None) -> Decimal | None:
        if a is None or b is None:
            return None
        return b - a

    sectors_before = {e.sector: e.weight_pct for e in current.sector_exposures}
    sectors_after = {e.sector: e.weight_pct for e in simulated.sector_exposures}

    sector_shifts = {
        sector: (sectors_after.get(sector, ZERO) - sectors_before.get(sector, ZERO))
        for sector in sorted(set(sectors_before) | set(sectors_after))
    }

    return SimulationDeltas(
        market_value=diff(current.market_value, simulated.market_value),
        cost_basis=diff(current.cost_basis, simulated.cost_basis),
        cash_balance=diff(current.cash_balance, simulated.cash_balance),
        total_value=diff(current.total_value, simulated.total_value),
        realized_pnl=diff(current.realized_pnl, simulated.realized_pnl),
        unrealized_pnl=diff(current.unrealized_pnl, simulated.unrealized_pnl),
        total_pnl=diff(current.total_pnl, simulated.total_pnl),
        total_return_pct=diff(current.total_return_pct, simulated.total_return_pct),
        position_count=simulated.position_count - current.position_count,
        diversification_index=diff(
            current.diversification_index, simulated.diversification_index
        ),
        effective_sectors=diff(current.effective_sectors, simulated.effective_sectors),
        effective_positions=diff(
            current.effective_positions, simulated.effective_positions
        ),
        largest_position_pct=diff(
            current.largest_position_pct, simulated.largest_position_pct
        ),
        sector_weight_shifts={k: v for k, v in sector_shifts.items() if v != ZERO},
    )
