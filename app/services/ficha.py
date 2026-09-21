"""La ficha de compra: TODO lo que hay que mirar de una empresa, en un lugar.

Es la fuente única. La interfaz, el script que genera el informe en markdown
(`scripts/ficha.py`) y el diario de decisiones leen de aquí, de modo que dos
pantallas nunca pueden contradecirse sobre la misma empresa.

No llama al proveedor: lee lo ya guardado, igual que el resto de servicios.
Que los datos estén viejos NO se esconde, se declara en `freshness`.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.models import Asset, Portfolio
from app.repositories import market as market_repo
from app.repositories import portfolio as portfolio_repo
from app.schemas.ficha import (
    AnalystRead,
    CalendarRead,
    FichaResponse,
    Flag,
    Freshness,
    FundProfileRead,
    HealthRead,
    HoldingRead,
    PeerRead,
    PortfolioContext,
    SizingRead,
)
from app.schemas.opportunity import OpportunityRead, OpportunityResponse
from app.services import asset_class as asset_class_service
from app.services import exposure as exposure_service
from app.services import opportunities as opportunity_service
from app.services import universe as universe_service
from app.services.exposure import exposure_bucket
from app.services.flags import build_flags, summarize
from app.services.health import HealthSnapshot, extract_health
from app.services.pnl import replay_ledger
from app.services.portfolio import build_positions
from app.services.sizing import size_position

MAX_PEERS = 5

# Los fundamentales se refrescan un par de veces por semana; más viejos que
# esto, algo del planificador no está funcionando y conviene decirlo.
FUNDAMENTALS_STALE_DAYS = 10


def _health_read(snapshot: HealthSnapshot) -> HealthRead:
    calendar, analyst = snapshot.calendar, snapshot.analyst
    return HealthRead(
        has_data=snapshot.has_data,
        applies=snapshot.applies,
        not_applicable_reason=snapshot.not_applicable_reason,
        reporting_currency=snapshot.reporting_currency,
        quote_currency=snapshot.quote_currency,
        currency_mismatch=snapshot.currency_mismatch,
        net_debt_to_ebitda=snapshot.net_debt_to_ebitda,
        fcf_margin=snapshot.fcf_margin,
        fcf_yield=snapshot.fcf_yield,
        operating_margin=snapshot.operating_margin,
        current_ratio=snapshot.current_ratio,
        payout_ratio=snapshot.payout_ratio,
        calendar=CalendarRead(
            next_earnings=calendar.next_earnings,
            earnings_is_estimate=calendar.earnings_is_estimate,
            days_to_earnings=calendar.days_to_earnings,
            ex_dividend=calendar.ex_dividend,
            days_to_ex_dividend=calendar.days_to_ex_dividend,
        ),
        analyst=AnalystRead(
            target_mean=analyst.target_mean,
            analysts=analyst.analysts,
            recommendation=analyst.recommendation,
            upside_pct=analyst.upside_pct,
        ),
        caveats=list(snapshot.caveats),
        unavailable=dict(snapshot.unavailable),
    )


def _peers(
    row: OpportunityRead,
    asset: Asset,
    rows: list[OpportunityRead],
    assets_by_symbol: dict[str, Asset],
) -> tuple[list[PeerRead], int | None, int | None]:
    """Las mejores empresas del mismo sector y la posición de esta entre ellas.

    Solo entre empresas comparables (acciones, ADR, REIT): un ETF sectorial no
    es una empresa y su P/E es el de su cesta.
    """
    group = opportunity_service.sector_group(asset)
    if group is None:
        return [], None, None

    members = [
        r for r in rows
        if (a := assets_by_symbol.get(r.symbol)) is not None
        and opportunity_service.sector_group(a) == group
    ]
    members.sort(key=lambda r: r.rank)
    position = next((i for i, r in enumerate(members, start=1) if r.symbol == row.symbol), None)
    peers = [
        PeerRead(
            symbol=r.symbol, name=r.name, rank=r.rank, score=r.score,
            grade=r.assessment.grade, grade_label=r.assessment.label,
            trailing_pe=r.value.inputs.get("trailing_pe"),
        )
        for r in members
        if r.symbol != row.symbol
    ][:MAX_PEERS]
    return peers, position, len(members)


@dataclass(frozen=True)
class _PortfolioState:
    context: PortfolioContext
    position_count: int
    # Capital por defecto para dimensionar: posiciones valoradas + efectivo
    # POSITIVO. Un efectivo negativo -depósitos sin registrar- no es capital.
    capital: Decimal | None
    holding_value: Decimal


def _portfolio_state(
    db: Session, portfolio: Portfolio, asset: Asset, bucket: str | None
) -> _PortfolioState:
    replay = replay_ledger(portfolio_repo.get_transactions(db, portfolio.id), strict=False)
    positions, market_value, _ = build_positions(db, portfolio, replay)

    holding: HoldingRead | None = None
    holding_value = Decimal(0)
    bucket_weight: float | None = None
    for position in positions:
        if position.symbol == asset.symbol:
            holding_value = position.market_value or Decimal(0)
            holding = HoldingRead(
                quantity=str(position.quantity),
                weight_pct=float(position.weight_pct) if position.weight_pct is not None else None,
                market_value=(
                    str(position.market_value) if position.market_value is not None else None
                ),
                unrealized_return_pct=(
                    float(position.unrealized_return_pct)
                    if position.unrealized_return_pct is not None
                    else None
                ),
            )

    if bucket is not None and market_value and market_value > Decimal(0):
        in_bucket = sum(
            (p.market_value for p in positions
             if p.exposure_bucket == bucket and p.market_value is not None),
            Decimal(0),
        )
        bucket_weight = float(in_bucket / market_value * 100)

    capital = (market_value or Decimal(0)) + max(replay.cash_balance, Decimal(0))
    context = PortfolioContext(
        portfolio_id=portfolio.id,
        base_currency=portfolio.base_currency,
        position_count=len(positions),
        holding=holding,
        bucket=bucket,
        bucket_weight_pct=bucket_weight,
        total_value=str(market_value) if market_value is not None else None,
    )
    return _PortfolioState(
        context=context,
        position_count=len(positions),
        capital=capital if capital > 0 else None,
        holding_value=holding_value,
    )


def compute_sizing(
    asset: Asset,
    row: OpportunityRead,
    state: _PortfolioState,
    *,
    capital: Decimal | None = None,
    risk_budget_pct: float | None = None,
    max_position_pct: float | None = None,
) -> SizingRead:
    """Dimensiona la posición. `capital` del usuario manda sobre el de la cartera."""
    effective_capital = capital if capital is not None else state.capital
    current_pct = 0.0
    if effective_capital and effective_capital > 0:
        current_pct = float(state.holding_value / effective_capital * 100)

    result = size_position(
        max_drawdown=row.risk.inputs.get("max_drawdown"),
        asset_class=asset_class_service.classify(asset).asset_class,
        risk_budget_pct=(
            risk_budget_pct if risk_budget_pct is not None else settings.position_risk_budget_pct
        ),
        max_position_pct=(
            max_position_pct
            if max_position_pct is not None
            else settings.position_max_weight_pct
        ),
        current_weight_pct=current_pct,
        capital=effective_capital,
        currency_differs=asset.currency.upper() != state.context.base_currency.upper(),
        is_diversified=_is_core_material(asset),
    )
    return SizingRead(**vars(result), base_currency=state.context.base_currency)


# Calificaciones que cuentan como GRADO DE INVERSIÓN. La frontera está entre
# BBB y BB y no es una convención más: por debajo de ella hay fondos de
# pensiones y mandatos institucionales que no pueden comprar, así que en una
# crisis el comprador marginal desaparece justo cuando hace falta. Es la
# diferencia entre «paga más» y «paga más por una razón».
#
# `us_government` NO está, y dejarlo fuera es el punto. No es una calificación
# más: es una etiqueta que SE SOLAPA con las otras, y sumarla las contaría dos
# veces. Comprobado sobre los cuatro fondos de bonos del universo, sin ella
# cada reparto suma exactamente 1,00:
#
#     TLT  aa 1,0000                                   + us_government 0,996
#     TIP  aa 0,9994                                   + us_government 0,9994
#     AGG  aa 0,7425 + aaa 0,0221 + a 0,1191 + bbb 0,1162 + bb 0,0001 = 1,00
_INVESTMENT_GRADE = ("aaa", "aa", "a", "bbb")


def _is_core_material(asset: Asset) -> bool:
    """¿Es un instrumento con el que alguien armaría el NÚCLEO de su cartera?

    Un fondo diversificado -de acciones o de bonos- lo es; una acción suelta y
    un fondo sectorial, no. La distinción importa porque el presupuesto de
    riesgo reparte entre APUESTAS, y un núcleo no es una apuesta.

    Se decide por el CUBO DE EXPOSICIÓN y no por la clase: un ETF sectorial es
    `fondo_acciones` igual que VT, pero XLE concentra en energía y sí es una
    apuesta. El cubo ya distingue las dos cosas, y reusarlo evita que esta
    respuesta y la de la diversificación se separen.
    """
    from app.services.asset_class import AssetClass

    clase = asset_class_service.classify(asset).asset_class
    if clase not in (AssetClass.FONDO_ACCIONES, AssetClass.RENTA_FIJA):
        return False
    return exposure_bucket(asset) in (
        exposure_service.BROAD_EQUITY,
        exposure_service.FIXED_INCOME,
    )


def _fund_profile(asset: Asset, sizing: SizingRead | None) -> FundProfileRead | None:
    """Composición, coste y calidad crediticia. None si no es un fondo."""
    perfil = asset.fund_profile
    if perfil is None:
        return None

    ratings = perfil.credit_ratings or None
    grado = None
    if ratings:
        grado = min(100.0, sum(ratings.get(k, 0.0) for k in _INVESTMENT_GRADE) * 100)

    # El coste EN DINERO sobre la posición que el sizing sugiere. Un 0,75%
    # anual no se siente; «US$ 7,50 al año sobre los US$ 1.000 que te propone
    # esta misma ficha» sí, y es la misma cifra.
    importe = None
    if (
        sizing is not None
        and sizing.target_amount is not None
        and perfil.expense_ratio is not None
    ):
        importe = (
            sizing.target_amount * Decimal(str(perfil.expense_ratio))
        ).quantize(Decimal("0.01"))

    return FundProfileRead(
        category=perfil.category,
        legal_type=perfil.legal_type,
        stock_position=perfil.stock_position,
        bond_position=perfil.bond_position,
        cash_position=perfil.cash_position,
        other_position=perfil.other_position,
        # Se redondea AQUÍ, en presentación, no en el dato: Yahoo devuelve
        # 0.00029999999 para el 0,03% de IVV y sin esto la ficha enseñaría
        # «0,029999999%». Misma regla que con el P&L que redondea a cero.
        expense_ratio_pct=(
            None if perfil.expense_ratio_pct is None
            else round(perfil.expense_ratio_pct, 3)
        ),
        expense_amount=importe,
        credit_ratings=ratings,
        investment_grade_pct=None if grado is None else round(grado, 1),
    )


def _freshness(
    db: Session, asset: Asset, snapshot, today: dt.date
) -> Freshness:
    quote = market_repo.get_quotes(db, [asset.id]).get(asset.id)
    last_bar = market_repo.get_last_bar_date(db, asset.id)
    fresh = Freshness(
        price_time=(quote.quote_time or quote.fetched_at) if quote else None,
        price_is_stale=bool(quote.is_stale) if quote else None,
        last_bar_date=last_bar,
        fundamentals_as_of=snapshot.as_of if snapshot else None,
        fundamentals_age_days=(today - snapshot.as_of).days if snapshot else None,
    )

    if quote is None:
        fresh.notes.append("No hay precio guardado.")
    elif quote.is_stale:
        fresh.notes.append("El último precio se marcó como obsoleto: puede no ser el actual.")
    if last_bar is not None and (today - last_bar).days > settings.price_series_max_age_days:
        fresh.notes.append(
            f"El histórico se detuvo el {last_bar.isoformat()} "
            f"({(today - last_bar).days} días)."
        )
    if snapshot is None:
        fresh.notes.append("No hay fundamentales guardados.")
    elif fresh.fundamentals_age_days is not None and (
        fresh.fundamentals_age_days > FUNDAMENTALS_STALE_DAYS
    ):
        fresh.notes.append(
            f"Los fundamentales son del {snapshot.as_of.isoformat()} "
            f"({fresh.fundamentals_age_days} días): puede haber resultados más recientes."
        )
    return fresh


@dataclass(frozen=True)
class _Scored:
    asset: Asset
    assets: list[Asset]
    response: OpportunityResponse
    row: OpportunityRead | None
    excluded_reason: str | None


def _score(db: Session, portfolio: Portfolio, symbol: str) -> _Scored:
    """Puntúa el universo (memoizado) y localiza la fila de `symbol`."""
    symbol = symbol.strip().upper()
    asset = market_repo.get_assets_by_symbols(db, [symbol]).get(symbol)
    if asset is None:
        raise NotFoundError(f"El símbolo {symbol} no está en el catálogo")

    # El universo de siempre MÁS este activo: si no forma parte del universo
    # ingestado se puntúa igualmente, con el mismo criterio que `?symbols=`.
    candidates: dict[str, Asset] = {
        a.symbol: a for a in universe_service.get_ingestion_universe(db)
    }
    candidates.setdefault(asset.symbol, asset)
    assets = sorted(candidates.values(), key=lambda a: a.symbol)

    response = opportunity_service.compute_opportunities(
        db, portfolio, assets=assets, limit=len(assets)
    )
    row = next((r for r in response.opportunities if r.symbol == asset.symbol), None)
    return _Scored(asset, assets, response, row, response.excluded.get(asset.symbol))


def build_sizing(
    db: Session,
    portfolio: Portfolio,
    symbol: str,
    *,
    capital: Decimal | None = None,
    risk_budget_pct: float | None = None,
    max_position_pct: float | None = None,
) -> SizingRead:
    """Solo el dimensionado, para recalcular con otros supuestos sin rehacer la ficha."""
    scored = _score(db, portfolio, symbol)
    if scored.row is None:
        raise NotFoundError(
            f"{scored.asset.symbol} no está en el ranking: "
            f"{scored.excluded_reason or 'faltan datos'}"
        )
    bucket = scored.row.exposure_bucket
    state = _portfolio_state(db, portfolio, scored.asset, bucket)
    return compute_sizing(
        scored.asset, scored.row, state,
        capital=capital, risk_budget_pct=risk_budget_pct, max_position_pct=max_position_pct,
    )


def build_ficha(db: Session, portfolio: Portfolio, symbol: str) -> FichaResponse:
    """Construye la ficha de `symbol` contra el ranking y la cartera actuales."""
    scored = _score(db, portfolio, symbol)
    asset, assets, response = scored.asset, scored.assets, scored.response
    row, excluded_reason = scored.row, scored.excluded_reason

    today = dt.date.today()
    snapshot = market_repo.get_latest_fundamental_with_raw(db, asset.id)
    price = row.current_price if row is not None else None
    if price is None:
        quote = market_repo.get_quotes(db, [asset.id]).get(asset.id)
        price = quote.price if quote else None

    health = extract_health(
        snapshot.raw if snapshot else None,
        quote_currency=asset.currency,
        sector=asset.sector,
        industry=asset.industry,
        price=price,
        today=today,
    )

    bucket = row.exposure_bucket if row is not None else exposure_bucket(asset)
    state = _portfolio_state(db, portfolio, asset, bucket)

    warnings = list(response.warnings)
    peers: list[PeerRead] = []
    sector_rank = sector_size = None
    sizing: SizingRead | None = None

    if row is not None:
        flags = build_flags(
            row, health,
            universe_size=response.universe_size,
            portfolio_position_count=state.position_count,
            concentration_threshold=settings.opportunity_sector_threshold,
        )
        assets_by_symbol = {a.symbol: a for a in assets}
        peers, sector_rank, sector_size = _peers(
            row, asset, list(response.opportunities), assets_by_symbol
        )
        sizing = compute_sizing(asset, row, state)
    else:
        flags = [Flag(
            code="not_ranked", level="red",
            title="No se pudo evaluar: faltan datos",
            detail=(
                (excluded_reason or "El activo no llegó al ranking.")
                + " Sin score ni calificación, no hay base para compararlo."
            ),
            evidence={"reason": excluded_reason},
        )]

    return FichaResponse(
        as_of=dt.datetime.now(dt.UTC),
        symbol=asset.symbol,
        name=asset.name,
        asset_type=asset.asset_type.value,
        sector=asset.sector,
        industry=asset.industry,
        currency=asset.currency,
        market_region=row.market_region if row is not None else None,
        opportunity=row,
        excluded_reason=excluded_reason if row is None else None,
        universe_size=response.universe_size,
        verdict=summarize(flags),
        flags=flags,
        health=_health_read(health),
        peers=peers,
        sector_rank=sector_rank,
        sector_size=sector_size,
        portfolio=state.context,
        fund=_fund_profile(asset, sizing),
        sizing=sizing,
        freshness=_freshness(db, asset, snapshot, today),
        warnings=warnings,
    )
