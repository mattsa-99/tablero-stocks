"""Los tipos de cambio se resuelven por DIVISA, no por posición.

Una cartera tiene decenas de posiciones y dos o tres divisas. Resolver el par
dentro del bucle multiplicaba las consultas por el número de posiciones para
devolver siempre el mismo USD->COP.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.models import Asset, AssetQuote, AssetType, FxRateDaily, Portfolio
from app.services import portfolio as portfolio_service
from tests.conftest import buy


@pytest.fixture
def cartera(db):
    """Doce posiciones en dólares: una sola divisa, doce oportunidades de N+1."""
    today = dt.date.today()
    portfolio = Portfolio(name="COP", base_currency="COP")
    db.add(portfolio)
    db.add(FxRateDaily(
        base_currency="USD", quote_currency="COP", date=today,
        rate=Decimal("4000"), fetched_at=dt.datetime.now(dt.UTC),
    ))
    db.flush()

    for i in range(12):
        item = Asset(symbol=f"SYM{i}", name=f"Activo {i}", currency="USD",
                     asset_type=AssetType.STOCK, sector="Technology")
        db.add(item)
        db.flush()
        db.add(AssetQuote(
            asset_id=item.id, price=100.0 + i, currency="USD",
            fetched_at=dt.datetime.now(dt.UTC), is_stale=False, source="test",
        ))
        db.add(buy(portfolio, item, dt.datetime.now(dt.UTC) - dt.timedelta(days=30)))
    db.commit()
    return portfolio


def _count_fx_queries(db, fn):
    seen = []

    def listener(conn, cursor, statement, params, context, executemany):
        if "fx_rates" in statement.lower():
            seen.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", listener)
    try:
        fn()
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", listener)
    return len(seen)


def test_one_query_per_currency_not_per_position(db, cartera):
    """Doce posiciones en la misma divisa son UNA consulta, no doce."""
    queries = _count_fx_queries(db, lambda: portfolio_service.get_summary(db, cartera))
    assert queries == 1, f"se esperaba 1 consulta a fx_rates y hubo {queries}"


def test_the_valuation_is_identical_after_batching(db, cartera):
    """Agrupar no puede cambiar ni un peso del resultado."""
    summary = portfolio_service.get_summary(db, cartera)

    # 12 compras de 10 unidades a 150 USD con el tipo congelado a 4000.
    assert summary.total_cost == Decimal("72000000")
    # Valoradas al precio actual (100..111 USD) y al tipo de hoy, 4000.
    esperado = sum(Decimal(10) * Decimal(100 + i) * Decimal(4000) for i in range(12))
    assert summary.market_value == esperado
    for position in summary.positions:
        assert position.fx_rate_to_base == Decimal("4000")


def test_a_missing_rate_is_still_a_missing_rate(db, cartera):
    """Deduplicar no puede convertir "no hay tipo" en un supuesto.

    Sin tipo, la posición se queda SIN valorar y aparece en
    `positions_without_price`. Asumir 1 erraría por ~4000x.
    """
    db.query(FxRateDaily).delete()
    db.commit()

    summary = portfolio_service.get_summary(db, cartera)
    assert summary.market_value is None
    assert len(summary.positions_without_price) == 12
