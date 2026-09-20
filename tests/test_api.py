"""Tests de la API completa, con la BD y los servicios reales.

Solo se sustituye el proveedor de mercado: todo lo demás -sesión, migraciones
de esquema, servicios, validación- es el código de producción.
"""

from __future__ import annotations

import datetime as dt

import pytest

UTC = dt.UTC


# El catálogo arranca vacío: el universo se siembra con ?symbols, que es el
# mecanismo ad hoc previsto para candidatos que aún no están en la BD.
UNIVERSE = "CHEAP,SOLID,FAIR,PRICEY,EXPENSIVE,RISKY"


def opportunities(client, portfolio_id, **params):
    query = "".join(f"&{k}={v}" for k, v in params.items())
    return client.get(
        f"/api/opportunities?portfolio_id={portfolio_id}&symbols={UNIVERSE}{query}"
    )


def yesterday() -> str:
    return (dt.datetime.now(UTC) - dt.timedelta(days=1)).isoformat()


def buy(client, portfolio_id, symbol, qty="10", price="100", fx="4000", when=None):
    return client.post(
        f"/api/transactions?portfolio_id={portfolio_id}",
        json={
            "type": "BUY",
            "symbol": symbol,
            "quantity": qty,
            "price": price,
            "executed_at": when or yesterday(),
            "currency": "USD",
            "fx_rate_to_base": fx,
        },
    )


# --------------------------------------------------------------------------
# Salud y portafolios
# --------------------------------------------------------------------------


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_create_portfolio_defaults_to_cop(client):
    response = client.post("/api/portfolios", json={"name": "Secundario"})
    assert response.status_code == 201
    assert response.json()["base_currency"] == "COP"


def test_duplicate_portfolio_name_returns_409(client, portfolio_id):
    response = client.post("/api/portfolios", json={"name": "Principal"})
    assert response.status_code == 409


def test_unknown_portfolio_returns_404(client):
    assert client.get("/api/portfolios/999").status_code == 404


# --------------------------------------------------------------------------
# Transacciones
# --------------------------------------------------------------------------


def test_create_buy_and_read_back(client, portfolio_id):
    response = buy(client, portfolio_id, "CHEAP", qty="10", price="100", fx="4000")
    assert response.status_code == 201

    body = response.json()
    assert body["symbol"] == "CHEAP"
    assert "asset_id" not in body, "El cliente nunca debe ver el id interno"
    # -(10*100 + 0) * 4000
    assert float(body["net_cash_flow_base"]) == pytest.approx(-4_000_000)


def test_oversell_returns_422(client, portfolio_id):
    buy(client, portfolio_id, "CHEAP", qty="10", price="100")
    response = client.post(
        f"/api/transactions?portfolio_id={portfolio_id}",
        json={
            "type": "SELL",
            "symbol": "CHEAP",
            "quantity": "15",
            "price": "120",
            "executed_at": yesterday(),
            "currency": "USD",
            "fx_rate_to_base": "4000",
        },
    )
    assert response.status_code == 422
    assert "supera" in response.json()["detail"]


def test_missing_fx_is_rejected_not_assumed(client, portfolio_id):
    """Asumir paridad COP/USD equivocaría el coste por un factor de ~4000."""
    response = client.post(
        f"/api/transactions?portfolio_id={portfolio_id}",
        json={
            "type": "BUY",
            "symbol": "CHEAP",
            "quantity": "10",
            "price": "100",
            "executed_at": yesterday(),
            "currency": "USD",
        },
    )
    assert response.status_code == 422
    assert "tipo de cambio" in response.json()["detail"].lower()


def test_future_transaction_is_rejected(client, portfolio_id):
    future = (dt.datetime.now(UTC) + dt.timedelta(days=1)).isoformat()
    response = buy(client, portfolio_id, "CHEAP", when=future)
    assert response.status_code == 422


def test_transaction_history_is_chronological(client, portfolio_id):
    now = dt.datetime.now(UTC)
    for days in (5, 1, 3):
        buy(client, portfolio_id, "CHEAP", when=(now - dt.timedelta(days=days)).isoformat())

    rows = client.get(f"/api/portfolios/{portfolio_id}/transactions").json()
    dates = [row["executed_at"] for row in rows]
    assert dates == sorted(dates)


def test_delete_transaction_revalidates_ledger(client, portfolio_id):
    """Borrar una compra puede dejar descubierta una venta posterior."""
    now = dt.datetime.now(UTC)
    purchase = buy(
        client, portfolio_id, "CHEAP", qty="10", when=(now - dt.timedelta(days=5)).isoformat()
    ).json()
    client.post(
        f"/api/transactions?portfolio_id={portfolio_id}",
        json={
            "type": "SELL",
            "symbol": "CHEAP",
            "quantity": "10",
            "price": "120",
            "executed_at": (now - dt.timedelta(days=1)).isoformat(),
            "currency": "USD",
            "fx_rate_to_base": "4000",
        },
    )

    response = client.delete(f"/api/transactions/{purchase['id']}")
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Resumen y posiciones
# --------------------------------------------------------------------------


def test_summary_computes_pnl_end_to_end(client, portfolio_id):
    client.post(
        f"/api/transactions?portfolio_id={portfolio_id}",
        json={
            "type": "DEPOSIT",
            "cash_amount": "50000000",
            "executed_at": yesterday(),
            "currency": "COP",
            "fx_rate_to_base": "1",
        },
    )
    buy(client, portfolio_id, "CHEAP", qty="10", price="100", fx="4000")

    summary = client.get(f"/api/portfolios/{portfolio_id}").json()

    assert summary["base_currency"] == "COP"
    assert float(summary["total_cost"]) == pytest.approx(4_000_000)
    assert float(summary["cash_balance"]) == pytest.approx(46_000_000)
    assert len(summary["positions"]) == 1
    assert summary["positions"][0]["symbol"] == "CHEAP"
    assert summary["positions"][0]["market_value"] is not None
    assert float(summary["positions"][0]["weight_pct"]) == pytest.approx(100.0)


def test_position_without_price_is_flagged_not_zeroed(client, portfolio_id, provider):
    """Un precio ausente vale None, nunca 0: un 0 sería una pérdida del 100%."""
    provider.quotes = {}
    buy(client, portfolio_id, "CHEAP")

    summary = client.get(f"/api/portfolios/{portfolio_id}").json()
    position = summary["positions"][0]

    assert position["market_value"] is None
    assert position["unrealized_pnl"] is None
    assert "CHEAP" in summary["positions_without_price"]


def test_realized_pnl_after_partial_sale(client, portfolio_id):
    now = dt.datetime.now(UTC)
    buy(
        client, portfolio_id, "CHEAP", qty="10", price="100", fx="4000",
        when=(now - dt.timedelta(days=5)).isoformat(),
    )
    client.post(
        f"/api/transactions?portfolio_id={portfolio_id}",
        json={
            "type": "SELL",
            "symbol": "CHEAP",
            "quantity": "4",
            "price": "150",
            "executed_at": (now - dt.timedelta(days=1)).isoformat(),
            "currency": "USD",
            "fx_rate_to_base": "4000",
        },
    )

    summary = client.get(f"/api/portfolios/{portfolio_id}").json()
    # 4 * (150-100) * 4000 = 800.000 COP
    assert float(summary["realized_pnl"]) == pytest.approx(800_000)
    assert float(summary["positions"][0]["quantity"]) == pytest.approx(6)


# --------------------------------------------------------------------------
# Oportunidades
# --------------------------------------------------------------------------


def test_opportunities_ranks_the_universe(client, portfolio_id):
    response = opportunities(client, portfolio_id)
    assert response.status_code == 200

    body = response.json()
    assert body["universe_size"] >= 5
    assert len(body["opportunities"]) <= 10

    scores = [row["score"] for row in body["opportunities"]]
    assert scores == sorted(scores, reverse=True), "Debe venir ordenado por score"
    assert all(0 <= score <= 100 for score in scores)
    assert [row["rank"] for row in body["opportunities"]] == list(
        range(1, len(scores) + 1)
    )


def test_score_equals_sum_of_contributions(client, portfolio_id):
    """La interpretabilidad es un requisito: el desglose debe reconstruir el score."""
    body = opportunities(client, portfolio_id).json()

    for row in body["opportunities"]:
        total = (
            row["value"]["contribution"]
            + row["momentum"]["contribution"]
            + row["diversification"]["contribution"]
            + row["risk"]["contribution"]
            + row["baseline"]
        )
        assert total == pytest.approx(row["score"], abs=0.05), row["symbol"]


def test_cheap_uptrending_stock_beats_expensive_falling_one(client, portfolio_id):
    body = opportunities(client, portfolio_id).json()
    ranked = {row["symbol"]: row["score"] for row in body["opportunities"]}

    assert ranked["CHEAP"] > ranked["EXPENSIVE"]
    assert ranked["CHEAP"] > ranked["RISKY"]


def test_risk_contribution_is_negative(client, portfolio_id):
    body = opportunities(client, portfolio_id).json()
    for row in body["opportunities"]:
        assert row["risk"]["weight"] < 0
        assert row["risk"]["contribution"] <= 0


def test_sector_concentration_penalizes_candidates(client, portfolio_id):
    """Con la cartera 100% en Technology, sus candidatos deben perder puntos."""
    baseline = opportunities(client, portfolio_id).json()
    before = {r["symbol"]: r["diversification"]["score"] for r in baseline["opportunities"]}

    buy(client, portfolio_id, "PRICEY", qty="100", price="100", fx="4000")

    after_body = opportunities(client, portfolio_id).json()
    after = {r["symbol"]: r["diversification"]["score"] for r in after_body["opportunities"]}

    assert after["EXPENSIVE"] < before["EXPENSIVE"], "Technology ya pesa 100%"
    assert after["CHEAP"] == pytest.approx(before["CHEAP"]), "Energy no debe verse afectado"

    penalized = next(r for r in after_body["opportunities"] if r["symbol"] == "EXPENSIVE")
    assert float(penalized["sector_weight_pct"]) == pytest.approx(100.0)
    assert any("penalización" in note for note in penalized["notes"])


def test_small_universe_is_rejected(client, portfolio_id, provider):
    """Con n<5 los rangos percentiles son ruido; mejor 422 que un ranking falso."""
    provider.metadata = {}
    provider.fundamentals = {}
    provider.history = {}
    buy(client, portfolio_id, "ONLYONE")

    response = client.get(f"/api/opportunities?portfolio_id={portfolio_id}")
    assert response.status_code == 422
    assert "candidatos" in response.json()["detail"]


def seed_universe(db, count=6):
    """Universo YA INGESTADO: activos con histórico y fundamentales guardados.

    Sembrarlo es lo que hace significativo el test de más abajo: con el
    universo vacío el endpoint no pide nada porque no hay nada que pedir, y
    la comprobación pasaría sin demostrar nada.
    """
    import math

    from app.models import Asset, AssetType, FundamentalSnapshot, PriceHistory

    today = dt.date.today()
    for n in range(count):
        asset = Asset(
            symbol=f"UNIV{n}", name=f"Universo {n}", sector="Technology",
            asset_type=AssetType.STOCK, currency="USD", is_universe=True,
        )
        db.add(asset)
        db.flush()
        price = 100.0
        for day in range(320):
            price *= (1 + 0.0012) * (1 + 0.02 * math.sin(day / 5))
            db.add(PriceHistory(
                asset_id=asset.id, date=today - dt.timedelta(days=320 - day),
                close=price, adj_close=price,
            ))
        db.add(FundamentalSnapshot(
            asset_id=asset.id, as_of=today, trailing_pe=18.0 + n,
            return_on_equity=0.20, profit_margin=0.15, debt_to_equity=50.0,
            revenue_growth=0.12, fetched_at=dt.datetime.now(UTC),
        ))
    db.commit()


def test_the_view_never_runs_the_weekly_job_in_the_request(
    client, portfolio_id, provider, db_of
):
    """Abrir la vista no puede disparar la sincronización completa.

    Es el fallo que motivó todo esto: el router llamaba a `full_refresh`
    -cuyo docstring dice "cadencia objetivo: dos veces por semana"- sobre los
    494 símbolos del universo, dentro de la petición. Medido el 19-09-2026
    contra la cartera real: 269 s de espera frente a 0,23 s de puntuar lo que
    ya estaba guardado.

    Se comprueba por los recursos CAROS -fundamentales y metadatos, los que
    pasan por `.info` a ~0,76 s por símbolo, y el histórico-. Las cotizaciones
    sí se piden, pero después de responder.
    """
    seed_universe(db_of)

    response = client.get(f"/api/opportunities?portfolio_id={portfolio_id}")

    assert response.status_code == 200, response.text
    assert response.json()["universe_size"] >= 5, "El universo tiene que existir"
    assert provider.call_count("fetch_fundamentals") == 0, (
        "Los fundamentales son trimestrales: no se piden al abrir una página"
    )
    assert provider.call_count("fetch_history") == 0, (
        "Las barras diarias son cierres inmutables: tampoco"
    )
    assert provider.call_count("fetch_metadata") == 0, (
        "Los metadatos tienen TTL de 30 días: menos todavía"
    )


def test_the_background_refresh_does_ask_for_prices(client, portfolio_id, provider, db_of):
    """El contrapunto: no pedir nada caro no significa no refrescar nada.

    Lo que sí cambia intradía es el precio -de ahí sale `fresh_trailing_pe`-,
    y eso se sigue trayendo, solo que después de haber respondido.
    """
    seed_universe(db_of)

    client.get(f"/api/opportunities?portfolio_id={portfolio_id}")

    assert provider.call_count("fetch_quotes") > 0


def test_a_symbol_typed_by_the_user_is_loaded_in_line(client, portfolio_id, provider):
    """Lo que el usuario escribe SÍ se trae ahora: lo está esperando.

    Es la excepción deliberada a "nada de red en la petición". Un candidato
    tecleado no tiene histórico, y sin él no se puede puntuar: pasarlo al
    refresco de fondo lo dejaría fuera del ranking que se pidió a propósito.
    Son un puñado de símbolos, no 494.
    """
    body = opportunities(client, portfolio_id).json()

    assert provider.call_count("fetch_history") > 0
    assert [o["symbol"] for o in body["opportunities"]], "Se puntúan los tecleados"


def test_the_response_says_whether_a_refresh_is_running(client, portfolio_id):
    """Sin esta señal la vista no sabe que merece la pena volver a preguntar."""
    body = opportunities(client, portfolio_id).json()
    assert body["refreshing"] is True


def test_asking_not_to_refresh_touches_no_network_at_all(client, portfolio_id, provider):
    """`refresh=false` es el camino que la vista usa para repreguntar."""
    opportunities(client, portfolio_id)
    provider.calls.clear()

    client.get(f"/api/opportunities?portfolio_id={portfolio_id}&refresh=false")

    assert provider.calls == [], "Repreguntar se sirve entero desde la base"


def test_response_carries_the_disclaimer(client, portfolio_id):
    body = opportunities(client, portfolio_id).json()
    assert "no una valoración absoluta" in body["disclaimer"]
    assert "RANKING RELATIVO" in body["disclaimer"]
    assert body["formula"].startswith("Score =")


# --------------------------------------------------------------------------
# Mercado
# --------------------------------------------------------------------------


def test_market_refresh_reports_what_it_did(client, portfolio_id):
    buy(client, portfolio_id, "CHEAP")
    body = client.post("/api/market/refresh?force=true").json()

    assert body["quotes_updated"] >= 1
    assert body["fx_updated"] >= 1
    assert isinstance(body["warnings"], list)


def test_provider_failure_degrades_instead_of_500(client, portfolio_id, provider):
    """Un fallo de Yahoo no puede tumbar la lectura del portafolio."""
    buy(client, portfolio_id, "CHEAP")
    provider.fail_with = RuntimeError("Yahoo caído")

    response = client.get(f"/api/portfolios/{portfolio_id}")
    assert response.status_code == 200
    assert response.json()["positions"][0]["symbol"] == "CHEAP"


def test_opportunities_declare_how_old_their_data_is(client, portfolio_id):
    body = opportunities(client, portfolio_id).json()
    fresh = body["freshness"]
    assert fresh["prices_newest"] is not None
    assert fresh["fundamentals_newest"] is not None
    assert fresh["fundamentals_oldest"] <= fresh["fundamentals_newest"]
    row = body["opportunities"][0]
    assert row["price_as_of"] is not None
    assert row["fundamentals_as_of"] is not None
