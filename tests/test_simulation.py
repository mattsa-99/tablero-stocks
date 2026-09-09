"""Tests del simulador What-If.

El test más importante de este archivo es `test_simulation_writes_nothing`: si
falla, el simulador está corrompiendo datos reales del usuario, que es el peor
fallo posible de esta funcionalidad.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models import Asset, PriceHistory, Transaction
from app.services.simulation import (
    diversification_index,
    effective_holdings,
    herfindahl,
)

UTC = dt.UTC


def yesterday() -> str:
    return (dt.datetime.now(UTC) - dt.timedelta(days=1)).isoformat()


def buy_api(client, portfolio_id, symbol, qty="10", price="100", fx="4000"):
    return client.post(
        f"/api/transactions?portfolio_id={portfolio_id}",
        json={
            "type": "BUY",
            "symbol": symbol,
            "quantity": qty,
            "price": price,
            "executed_at": yesterday(),
            "currency": "USD",
            "fx_rate_to_base": fx,
        },
    )


def simulate(client, portfolio_id, trades):
    return client.post(f"/api/portfolios/{portfolio_id}/simulate", json={"trades": trades})


@pytest.fixture
def funded(client, portfolio_id):
    """Cartera con dos posiciones en sectores distintos."""
    client.post(
        f"/api/transactions?portfolio_id={portfolio_id}",
        json={
            "type": "DEPOSIT",
            "cash_amount": "200000000",
            "executed_at": yesterday(),
            "currency": "COP",
            "fx_rate_to_base": "1",
        },
    )
    buy_api(client, portfolio_id, "CHEAP", qty="40", price="100")  # Energy
    buy_api(client, portfolio_id, "SOLID", qty="20", price="100")  # Healthcare
    # Carga el resumen una vez para poblar cotizaciones y FX, igual que hace el
    # dashboard antes de que el usuario abra el simulador. Sin esto no hay
    # valor de mercado y los pesos por sector salen vacíos.
    client.get(f"/api/portfolios/{portfolio_id}")
    return portfolio_id


# --------------------------------------------------------------------------
# Aislamiento: EL requisito
# --------------------------------------------------------------------------


def test_simulation_writes_nothing(client, funded, db_of):
    """Cuenta filas antes y después. Es la garantía, no una comprobación suave."""
    before = db_of.execute(
        select(func.count()).select_from(Transaction)
    ).scalar_one()
    assets_before = db_of.execute(select(func.count()).select_from(Asset)).scalar_one()

    response = simulate(
        client,
        funded,
        [
            {"type": "BUY", "symbol": "PRICEY", "quantity": "50", "price": "120",
             "currency": "USD", "fx_rate_to_base": "4150"},
            {"type": "SELL", "symbol": "CHEAP", "quantity": "10", "price": "150",
             "currency": "USD", "fx_rate_to_base": "4150"},
        ],
    )
    assert response.status_code == 200

    after = db_of.execute(select(func.count()).select_from(Transaction)).scalar_one()
    assets_after = db_of.execute(select(func.count()).select_from(Asset)).scalar_one()

    assert after == before, "La simulación escribió transacciones en la base de datos"
    assert assets_after == assets_before, "La simulación creó activos"
    assert response.json()["persisted"] is False


def test_unknown_symbol_creates_no_asset(client, funded, db_of):
    """Un símbolo nuevo se representa con un stub desconectado, no con una fila."""
    before = db_of.execute(select(func.count()).select_from(Asset)).scalar_one()

    response = simulate(
        client,
        funded,
        [{"type": "BUY", "symbol": "BRANDNEW", "quantity": "10", "price": "50",
          "currency": "USD", "fx_rate_to_base": "4150", "sector": "Utilities"}],
    )
    assert response.status_code == 200
    assert db_of.execute(select(func.count()).select_from(Asset)).scalar_one() == before
    assert db_of.scalar(select(Asset).where(Asset.symbol == "BRANDNEW")) is None


def test_repeated_simulation_is_stable(client, funded):
    """Simular dos veces lo mismo da lo mismo: no hay estado acumulado."""
    trades = [{"type": "BUY", "symbol": "PRICEY", "quantity": "10", "price": "120",
               "currency": "USD", "fx_rate_to_base": "4150"}]
    first = simulate(client, funded, trades).json()
    second = simulate(client, funded, trades).json()

    assert first["simulated_state"]["market_value"] == second["simulated_state"]["market_value"]
    assert first["deltas"]["cash_balance"] == second["deltas"]["cash_balance"]


# --------------------------------------------------------------------------
# Corrección del cálculo
# --------------------------------------------------------------------------


def test_buy_moves_cash_and_cost(client, funded):
    """Comprar 10 a 120 con FX 4150 = 4.980.000 COP fuera de caja."""
    body = simulate(
        client, funded,
        [{"type": "BUY", "symbol": "PRICEY", "quantity": "10", "price": "120",
          "currency": "USD", "fx_rate_to_base": "4150"}],
    ).json()

    assert float(body["deltas"]["cash_balance"]) == pytest.approx(-4_980_000)
    assert float(body["deltas"]["cost_basis"]) == pytest.approx(4_980_000)
    assert body["deltas"]["position_count"] == 1


def test_sell_realizes_pnl_without_persisting(client, funded):
    """Vender 10 de CHEAP compradas a 100 y vendidas a 150, FX 4150.

    Ingreso 10*150*4150 = 6.225.000; coste retirado 10*100*4000 = 4.000.000.
    Realizado = 2.225.000 COP.
    """
    body = simulate(
        client, funded,
        [{"type": "SELL", "symbol": "CHEAP", "quantity": "10", "price": "150",
          "currency": "USD", "fx_rate_to_base": "4150"}],
    ).json()

    assert float(body["deltas"]["realized_pnl"]) == pytest.approx(2_225_000)
    assert float(body["deltas"]["cash_balance"]) == pytest.approx(6_225_000)


def test_oversell_is_rejected(client, funded):
    """Un simulador que permite lo imposible no sirve para decidir."""
    response = simulate(
        client, funded,
        [{"type": "SELL", "symbol": "CHEAP", "quantity": "9999", "price": "150",
          "currency": "USD", "fx_rate_to_base": "4150"}],
    )
    assert response.status_code == 422
    assert "supera" in response.json()["detail"]


def test_current_state_matches_the_real_summary(client, funded):
    """El «antes» del simulador y el resumen real deben coincidir."""
    summary = client.get(f"/api/portfolios/{funded}").json()
    body = simulate(
        client, funded,
        [{"type": "DEPOSIT", "cash_amount": "1000", "currency": "COP",
          "fx_rate_to_base": "1"}],
    ).json()

    assert float(body["current_state"]["cash_balance"]) == pytest.approx(
        float(summary["cash_balance"])
    )
    assert float(body["current_state"]["cost_basis"]) == pytest.approx(
        float(summary["total_cost"])
    )


# --------------------------------------------------------------------------
# Diversificación
# --------------------------------------------------------------------------


def test_concentrating_lowers_diversification(client, funded):
    """Cargar el sector que ya pesa más debe empeorar el índice."""
    body = simulate(
        client, funded,
        [{"type": "BUY", "symbol": "CHEAP", "quantity": "200", "price": "100",
          "currency": "USD", "fx_rate_to_base": "4000"}],
    ).json()

    before = float(body["current_state"]["diversification_index"])
    after = float(body["simulated_state"]["diversification_index"])
    assert after < before
    assert float(body["deltas"]["diversification_index"]) < 0

    shifts = body["deltas"]["sector_weight_shifts"]
    assert float(shifts["Energy"]) > 0, "Energy debe ganar peso"
    assert float(shifts["Healthcare"]) < 0, "Healthcare debe perderlo"


def test_new_sector_improves_diversification(client, funded):
    body = simulate(
        client, funded,
        [{"type": "BUY", "symbol": "FAIR", "quantity": "20", "price": "100",
          "currency": "USD", "fx_rate_to_base": "4000"}],
    ).json()
    assert float(body["deltas"]["diversification_index"]) > 0


def test_sector_shift_is_reported_in_points(client, funded):
    """«Tu exposición pasará del X% al Y%» sale de aquí."""
    body = simulate(
        client, funded,
        [{"type": "BUY", "symbol": "CHEAP", "quantity": "40", "price": "100",
          "currency": "USD", "fx_rate_to_base": "4000"}],
    ).json()

    def weights(state):
        return {e["sector"]: float(e["weight_pct"]) for e in body[state]["sector_exposures"]}

    before = weights("current_state")
    after = weights("simulated_state")
    shift = float(body["deltas"]["sector_weight_shifts"]["Energy"])

    assert shift == pytest.approx(after["Energy"] - before["Energy"], abs=0.01)


# --------------------------------------------------------------------------
# Métricas de concentración
# --------------------------------------------------------------------------


def test_hhi_is_one_when_fully_concentrated():
    assert herfindahl([Decimal("1")]) == Decimal("1")


def test_hhi_falls_with_equal_parts():
    assert herfindahl([Decimal("0.25")] * 4) == Decimal("0.25")


def test_diversification_index_bounds():
    assert diversification_index([Decimal("1")]) == Decimal("0")
    four_equal = diversification_index([Decimal("0.25")] * 4)
    assert four_equal == Decimal("75")


def test_diversification_distinguishes_equal_sector_counts():
    """Cinco sectores al 20% no es lo mismo que cinco con uno al 96%.

    Es la razón de usar HHI y no contar sectores: el conteo daría 5 en ambos.
    """
    balanced = diversification_index([Decimal("0.2")] * 5)
    skewed = diversification_index(
        [Decimal("0.96"), Decimal("0.01"), Decimal("0.01"), Decimal("0.01"), Decimal("0.01")]
    )
    assert balanced > skewed
    assert skewed < Decimal("10")


def test_effective_holdings_counts_equivalent_positions():
    assert effective_holdings([Decimal("0.25")] * 4) == Decimal("4")
    assert effective_holdings([Decimal("1")]) == Decimal("1")


def test_price_history_table_is_without_rowid(db_of):
    """Se verifica el DDL real, no el modelo: el modelo puede decir una cosa
    y la migración haber creado otra."""
    from sqlalchemy import text

    row = db_of.execute(
        text("SELECT sql FROM sqlite_master WHERE name = 'price_history'")
    ).scalar_one()
    assert "WITHOUT ROWID" in row.upper()
    assert "fetched_at" not in row
    assert "source" not in row


def test_price_history_has_no_provenance_columns():
    columns = {c.name for c in PriceHistory.__table__.columns}
    assert "source" not in columns
    assert "fetched_at" not in columns
    assert columns == {
        "asset_id", "date", "open", "high", "low", "close", "adj_close", "volume"
    }
