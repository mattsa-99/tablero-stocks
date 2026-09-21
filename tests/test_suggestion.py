"""Tests del borrado de portafolios, el catálogo y el motor de sugerencia."""

from __future__ import annotations

import datetime as dt
import math

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.models import Asset, AssetType, FundamentalSnapshot, PriceHistory, Transaction
from app.services import catalog
from app.services import correlation as corr
from app.services.suggestion import concentration_factor, correlation_factor

UTC = dt.UTC


# ==========================================================================
# Feature 1: borrado de portafolio
# ==========================================================================


def yesterday() -> str:
    return (dt.datetime.now(UTC) - dt.timedelta(days=1)).isoformat()


def test_delete_removes_the_portfolio_and_its_ledger(client, portfolio_id, db_of):
    client.post(
        f"/api/transactions?portfolio_id={portfolio_id}",
        json={"type": "DEPOSIT", "cash_amount": "1000", "executed_at": yesterday(),
              "currency": "COP", "fx_rate_to_base": "1"},
    )
    assert db_of.scalar(select(func.count()).select_from(Transaction)) == 1

    assert client.delete(f"/api/portfolios/{portfolio_id}").status_code == 204

    assert client.get(f"/api/portfolios/{portfolio_id}").status_code == 404
    assert db_of.scalar(select(func.count()).select_from(Transaction)) == 0, (
        "La cascada debe llevarse las transacciones"
    )


def test_delete_keeps_the_assets(client, portfolio_id, db_of):
    """Los activos son catálogo global: su histórico sirve a otras carteras."""
    client.post(
        f"/api/transactions?portfolio_id={portfolio_id}",
        json={"type": "BUY", "symbol": "CHEAP", "quantity": "1", "price": "100",
              "executed_at": yesterday(), "currency": "USD", "fx_rate_to_base": "4000"},
    )
    before = db_of.scalar(select(func.count()).select_from(Asset))

    client.delete(f"/api/portfolios/{portfolio_id}")

    assert db_of.scalar(select(func.count()).select_from(Asset)) == before


def test_deleting_one_portfolio_leaves_the_others(client):
    first = client.post("/api/portfolios", json={"name": "Uno"}).json()["id"]
    second = client.post("/api/portfolios", json={"name": "Dos"}).json()["id"]

    client.delete(f"/api/portfolios/{first}")

    remaining = [p["id"] for p in client.get("/api/portfolios").json()]
    assert remaining == [second]


def test_deleting_an_unknown_portfolio_is_404(client):
    assert client.delete("/api/portfolios/9999").status_code == 404


def test_the_delete_button_and_modal_exist(client):
    html = client.get("/").text
    assert "open-delete-portfolio" in html
    assert 'x-data="deletePortfolioModal()"' in html
    assert "text-red-500" in html and "hover:text-red-700" in html
    assert "no se puede deshacer" in html
    # Es un diálogo destructivo: debe anunciarse como alerta, no como diálogo
    # cualquiera, para que un lector de pantalla lo interrumpa.
    assert 'role="alertdialog"' in html


# ==========================================================================
# Feature 2: catálogo e ingesta por lotes
# ==========================================================================


def test_catalog_covers_stocks_etfs_and_crypto():
    stats = catalog.catalog_stats()
    assert stats["TOTAL"] >= 150
    assert stats["STOCK"] >= 100
    assert stats["ETF"] >= 40
    assert stats["CRYPTO"] >= 10


def test_catalog_includes_the_requested_symbols():
    symbols = {entry["symbol"] for entry in catalog.load_catalog()}
    for expected in ("SPY", "QQQ", "VTI", "BTC-USD", "ETH-USD", "GC=F"):
        assert expected in symbols


# Los 11 sectores que devuelve Yahoo. Cualquier otra cadena en el catálogo
# sería una clasificación inventada a mano.
YAHOO_SECTORS = {
    "Basic Materials", "Communication Services", "Consumer Cyclical",
    "Consumer Defensive", "Energy", "Financial Services", "Healthcare",
    "Industrials", "Real Estate", "Technology", "Utilities",
}


def test_catalog_does_not_invent_sectors():
    """El sector se copia del proveedor o se deja nulo. Nunca se inventa.

    Antes esto se comprobaba exigiendo que solo los ETF tuvieran sector, porque
    el catálogo se escribía a mano y cualquier sector en una acción solo podía
    ser inventado. Ahora `scripts/build_catalog.py` lo trae verificado de Yahoo
    junto al precio y la divisa, así que las acciones SÍ pueden traerlo y la
    forma es mejor: el buscador lo muestra sin esperar a la ingesta.

    Lo que no ha cambiado es la prohibición de fabricarlo, y eso es lo que se
    fija aquí: todo sector presente debe ser uno de los de Yahoo.
    """
    invented = sorted(
        {
            e["sector"]
            for e in catalog.load_catalog()
            if e.get("sector") and e["sector"] not in YAHOO_SECTORS
        }
    )
    assert not invented, f"Sectores que Yahoo no usa: {invented}"


def test_seeding_the_catalog_is_idempotent(db):
    first = catalog.seed_catalog(db)
    second = catalog.seed_catalog(db)

    assert first["created"] > 150
    assert second["created"] == 0


def test_catalog_assets_are_searchable_but_not_ingested(db, monkeypatch):
    """LA distinción que hace escalable el sistema.

    Miles de filas en el catálogo cuestan bytes; miles de símbolos en el
    universo de ingesta costarían llamadas y agotarían el rate limit.

    Se comprueba sobre un catálogo sintético y no sobre el de disco A PROPÓSITO.
    El catálogo real declara hoy a TODOS sus miembros como universo, así que
    medirlo sobre él no distinguiría "el mecanismo funciona" de "da la
    casualidad de que coinciden". Lo que debe seguir siendo cierto es que una
    entrada sin `"universe": true` es buscable y NO se ingesta.
    """
    from app.services import universe as universe_service

    monkeypatch.setattr(
        catalog,
        "load_catalog",
        lambda: [
            {"symbol": "SOLOBUSCABLE", "name": "Solo en el buscador",
             "asset_type": "STOCK", "currency": "USD"},
            {"symbol": "INGESTADO", "name": "Se refresca a diario",
             "asset_type": "STOCK", "currency": "USD", "universe": True},
        ],
    )
    catalog.seed_catalog(db)

    assert catalog.catalog_size(db) == 2
    ingested = [a.symbol for a in universe_service.get_ingestion_universe(db)]
    assert ingested == ["INGESTADO"]


def test_seeding_never_overwrites_provider_data(db):
    """El dato de Yahoo es más fiable y reciente que este archivo."""
    db.add(Asset(symbol="AAPL", name="Nombre del proveedor",
                 sector="Technology", currency="USD"))
    db.commit()

    catalog.seed_catalog(db)

    stored = db.scalar(select(Asset).where(Asset.symbol == "AAPL"))
    assert stored.name == "Nombre del proveedor"
    assert stored.sector == "Technology"


def test_ingestion_runs_in_batches_with_a_pause(db, monkeypatch):
    """Trocear cuesta segundos de reloj y evita perder una sincronización."""
    from app.core.config import settings
    from app.services import ingestion
    from app.services import universe as universe_service
    from tests.fakes import FakeProvider, metadata, quote, synthetic_series

    symbols = [f"SYM{i:02d}" for i in range(7)]
    universe_service.seed_universe(db, symbols)

    monkeypatch.setattr(settings, "ingestion_batch_size", 3)
    monkeypatch.setattr(settings, "ingestion_batch_delay_seconds", 0.0)

    provider = FakeProvider(
        quotes={s: quote(s, 100.0) for s in symbols},
        history={s: synthetic_series(100.0, 30) for s in symbols},
        metadata={s: metadata(s, "Technology") for s in symbols},
    )
    run = ingestion.run_sync(db, provider)

    # 7 símbolos en lotes de 3 -> 3 lotes, cada uno con su llamada de cotización.
    assert provider.call_count("fetch_quotes") == 3
    assert run.symbols_requested == 7
    assert run.quotes_updated == 7


def test_the_delay_is_between_batches_not_after_the_last(db, monkeypatch):
    """Dormir al final alarga el trabajo sin proteger nada."""
    from app.core.config import settings
    from app.services import ingestion
    from app.services import universe as universe_service
    from tests.fakes import FakeProvider, metadata, quote

    sleeps: list[float] = []
    monkeypatch.setattr(ingestion.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(settings, "ingestion_batch_size", 2)
    monkeypatch.setattr(settings, "ingestion_batch_delay_seconds", 1.5)

    symbols = [f"S{i}" for i in range(6)]  # 3 lotes
    universe_service.seed_universe(db, symbols)
    ingestion.run_sync(
        db,
        FakeProvider(quotes={s: quote(s, 10.0) for s in symbols},
                     metadata={s: metadata(s, "Technology") for s in symbols}),
    )

    assert sleeps == [1.5, 1.5], "3 lotes -> 2 pausas, no 3"


def test_lazy_ingestion_only_fetches_what_is_missing(db):
    from app.services import ingestion
    from tests.fakes import FakeProvider, metadata, quote, synthetic_series

    known = Asset(symbol="TIENE", currency="USD")
    unknown = Asset(symbol="NOTIENE", currency="USD")
    db.add_all([known, unknown])
    db.commit()
    db.add(PriceHistory(asset_id=known.id, date=dt.date.today(), close=100.0))
    db.commit()

    provider = FakeProvider(
        quotes={"NOTIENE": quote("NOTIENE", 50.0)},
        history={"NOTIENE": synthetic_series(50.0, 30)},
        metadata={"NOTIENE": metadata("NOTIENE", "Energy")},
    )
    ingestion.ensure_data_for(db, provider, [known, unknown])

    called = [args[0] for name, args in provider.calls if name == "fetch_quotes"]
    assert called == [("NOTIENE",)], "Solo debe pedir el que no tenía histórico"


# ==========================================================================
# Feature 3: correlación y sugerencia
# ==========================================================================


def series(start: dt.date, values: list[float]) -> list[tuple[dt.date, float]]:
    return [(start + dt.timedelta(days=i), v) for i, v in enumerate(values)]


def test_correlation_pairs_by_date_not_by_position():
    """El error que produce un número plausible y falso.

    Dos series con calendarios distintos: emparejadas por posición darían
    correlación perfecta; por fecha, solo comparten dos días y no hay solape
    suficiente para afirmar nada.
    """
    base = dt.date(2026, 1, 5)
    left = corr.returns_by_date(series(base, [100, 101, 102, 103, 104]))
    right = corr.returns_by_date(
        [(base, 50.0), (base + dt.timedelta(days=2), 51.0), (base + dt.timedelta(days=4), 52.0)]
    )
    paired_left, paired_right = corr.paired(left, right)

    assert len(paired_left) == len(paired_right)
    assert len(paired_left) == 2, "Solo dos fechas en común"
    assert corr.pearson(paired_left, paired_right) is None, "Sin solape suficiente"


def test_identical_series_correlate_at_one():
    values = [100 * (1 + 0.01 * math.sin(i / 3)) for i in range(120)]
    returns = corr.to_returns(values)
    assert corr.pearson(returns, returns) == pytest.approx(1.0)


def test_opposite_series_correlate_at_minus_one():
    up = corr.to_returns([100 * (1.01**i) for i in range(120)])
    down = [-r for r in up]
    assert corr.pearson(up, down) == pytest.approx(-1.0)


def test_correlation_needs_a_minimum_overlap():
    short = [0.01, -0.02, 0.03]
    assert corr.pearson(short, short) is None


def test_a_constant_series_has_no_correlation():
    """Una serie sin varianza no tiene correlación definida; devolver 0 sería
    afirmar independencia."""
    flat = [0.0] * 120
    noisy = [0.01 * ((-1) ** i) for i in range(120)]
    assert corr.pearson(flat, noisy) is None


def test_low_correlation_reduces_portfolio_volatility():
    """El término cruzado de la varianza: si rho es bajo, resta."""
    base = [0.01 * ((-1) ** i) for i in range(150)]
    # Serie con la MISMA volatilidad pero desfasada: correlación ~ -1.
    hedge = [-v for v in base]

    impact = corr.volatility_impact(base, hedge, weight=0.30)
    assert impact is not None
    assert impact.correlation == pytest.approx(-1.0)
    assert impact.delta_pct < 0, "Un activo opuesto debe bajar la volatilidad"


def test_perfectly_correlated_addition_does_not_reduce_risk():
    base = [0.01 * ((-1) ** i) for i in range(150)]
    impact = corr.volatility_impact(base, list(base), weight=0.30)
    assert impact is not None
    assert impact.delta_pct == pytest.approx(0.0, abs=0.01)


def test_the_shown_volatility_numbers_are_mutually_consistent():
    """Los tres números que ve el usuario tienen que cuadrar entre sí.

    Antes se mostraba «de 29,7% a 27,9%» junto a «−6,3%»: el delta salía de la
    precisión interna y 27,9/29,7 daba −6,1%, no −6,3%. Ahora el delta es en
    puntos y se deriva de los mismos valores redondeados que se enseñan.
    """
    base = [0.012 * ((-1) ** i) + 0.0003 * i for i in range(200)]
    hedge = [-0.9 * v for v in base]

    impact = corr.volatility_impact(base, hedge, weight=0.10)
    assert impact is not None
    assert impact.delta_pp == pytest.approx(
        round(impact.simulated_pct - impact.current_pct, 1)
    )
    # La resta de lo que se muestra da exactamente el delta que se muestra.
    assert round(impact.current_pct - impact.simulated_pct, 1) == abs(impact.delta_pp)


def test_correlation_factor_bounds():
    assert correlation_factor(1.0) == pytest.approx(0.5)
    assert correlation_factor(0.0) == pytest.approx(1.0)
    assert correlation_factor(-1.0) == pytest.approx(1.5)


def test_unknown_correlation_is_neutral_never_a_bonus():
    """El fallo más traicionero del motor: premiar por una descorrelación que
    nadie ha medido."""
    assert correlation_factor(None) == pytest.approx(1.0)


def test_concentration_factor_penalises_hard_past_the_threshold():
    assert concentration_factor(0.0) == pytest.approx(1.0)
    assert concentration_factor(0.25) == pytest.approx(1.0)
    assert concentration_factor(0.60) < 0.7
    assert concentration_factor(1.0) == pytest.approx(0.25)


def test_concentration_factor_is_monotonic():
    values = [concentration_factor(w / 100) for w in range(101)]
    assert all(a >= b for a, b in zip(values, values[1:], strict=False))


# --------------------------------------------------------------------------
# El endpoint, de extremo a extremo
# --------------------------------------------------------------------------


def build_universe(db, *, concentrated_sector="Technology"):
    """Universo con histórico real para que haya correlaciones que medir."""
    today = dt.date.today()
    specs = [
        ("TECHA", "Technology", 0.0015, 1),
        ("TECHB", "Technology", 0.0014, 1),
        ("SALUD", "Healthcare", 0.0012, -1),
        ("ENERG", "Energy", 0.0013, -1),
        ("BANCO", "Financial Services", 0.0011, 1),
        ("CONSU", "Consumer Defensive", 0.0010, -1),
    ]
    created = {}
    for symbol, sector, drift, phase in specs:
        asset = Asset(symbol=symbol, name=f"{symbol} SA", sector=sector,
                      asset_type=AssetType.STOCK, currency="USD", is_universe=True)
        db.add(asset)
        db.flush()
        price = 100.0
        for day in range(320):
            price *= (1 + drift) * (1 + 0.02 * phase * math.sin(day / 5))
            db.add(PriceHistory(asset_id=asset.id,
                                date=today - dt.timedelta(days=320 - day),
                                close=price, adj_close=price))
        db.add(FundamentalSnapshot(asset_id=asset.id, as_of=today, trailing_pe=18.0,
                                   return_on_equity=0.20, profit_margin=0.15,
                                   debt_to_equity=50.0, revenue_growth=0.12,
                                   fetched_at=dt.datetime.now(UTC)))
        created[symbol] = asset
    spy = Asset(symbol="SPY", name="S&P 500", asset_type=AssetType.ETF,
                currency="USD", is_universe=True)
    db.add(spy)
    db.flush()
    db.add(FundamentalSnapshot(asset_id=spy.id, as_of=today, trailing_pe=25.0,
                               fetched_at=dt.datetime.now(UTC)))
    db.commit()
    return created


def test_suggestion_endpoint_returns_a_justified_pick(client, portfolio_id, db_of):
    build_universe(db_of)

    response = client.get(
        f"/api/portfolios/{portfolio_id}/suggested-stock?refresh=false"
    )
    assert response.status_code == 200

    body = response.json()
    assert body["suggestion"] is not None
    pick = body["suggestion"]

    assert pick["headline"].startswith("Te sugerimos ")
    assert pick["reasons"], "Debe justificarse con cifras"

    vi = pick["volatility_impact"]
    if vi is not None:
        # El delta mostrado es la resta exacta de los dos valores mostrados.
        assert vi["delta_pp"] == pytest.approx(
            round(vi["simulated_volatility_pct"] - vi["current_volatility_pct"], 1)
        )
        vol_reason = next((r for r in pick["reasons"] if "volatilidad" in r), None)
        if vol_reason is not None:
            assert "puntos porcentuales" in vol_reason, (
                "el impacto en volatilidad se expresa en puntos, no como % relativo"
            )

    assert body["assumed_weight_pct"] == 5.0
    # UN SOLO umbral en todo el sistema. Antes había dos -25% aquí y 30% en el
    # motor de oportunidades- y la misma concentración se penalizaba dos veces
    # con criterios distintos.
    assert body["concentration_threshold_pct"] == pytest.approx(
        settings.opportunity_sector_threshold * 100
    )
    assert "NO es asesoramiento financiero" in body["disclaimer"]


def test_the_final_score_is_the_product_of_its_factors(client, portfolio_id, db_of):
    """La fórmula debe poder verificarse desde la respuesta."""
    build_universe(db_of)
    body = client.get(
        f"/api/portfolios/{portfolio_id}/suggested-stock?refresh=false"
    ).json()

    factors = body["suggestion"]["factors"]
    expected = (
        factors["opportunity_score"]
        * factors["correlation_factor"]
        * factors["concentration_factor"]
    )
    assert factors["final_score"] == pytest.approx(expected, abs=0.05)


def test_a_concentrated_sector_is_penalised(client, portfolio_id, db_of):
    """Con la cartera cargada de tecnología, no debe sugerir más tecnología."""
    assets = build_universe(db_of)
    now = dt.datetime.now(UTC)

    for symbol in ("TECHA", "TECHB"):
        client.post(
            f"/api/transactions?portfolio_id={portfolio_id}",
            json={"type": "BUY", "symbol": symbol, "quantity": "500", "price": "100",
                  "executed_at": (now - dt.timedelta(days=5)).isoformat(),
                  "currency": "USD", "fx_rate_to_base": "4000"},
        )
    # Precio y tipo de cambio: sin AMBOS no hay valoración, y sin valoración no
    # hay pesos por sector que penalizar.
    from decimal import Decimal

    from app.models import AssetQuote, FxRateDaily

    for asset in assets.values():
        db_of.merge(AssetQuote(asset_id=asset.id, price=120.0, currency="USD",
                               quote_time=now, fetched_at=now))
    db_of.add(FxRateDaily(base_currency="USD", quote_currency="COP",
                          date=dt.date.today(), rate=Decimal("4000"), fetched_at=now))
    db_of.commit()

    body = client.get(
        f"/api/portfolios/{portfolio_id}/suggested-stock?refresh=false"
    ).json()

    pick = body["suggestion"]
    assert pick is not None
    assert pick["exposure_bucket"] != "Technology", (
        "Con Technology sobreponderado, la sugerencia debe salir de otro cubo"
    )


def test_assets_already_held_are_not_suggested(client, portfolio_id, db_of):
    """Es una sugerencia de SIGUIENTE compra, no de reforzar lo que ya tienes."""
    assets = build_universe(db_of)
    now = dt.datetime.now(UTC)
    from app.models import AssetQuote

    client.post(
        f"/api/transactions?portfolio_id={portfolio_id}",
        json={"type": "BUY", "symbol": "SALUD", "quantity": "100", "price": "100",
              "executed_at": (now - dt.timedelta(days=5)).isoformat(),
              "currency": "USD", "fx_rate_to_base": "4000"},
    )
    db_of.merge(AssetQuote(asset_id=assets["SALUD"].id, price=120.0, currency="USD",
                           quote_time=now, fetched_at=now))
    db_of.commit()

    body = client.get(
        f"/api/portfolios/{portfolio_id}/suggested-stock?refresh=false"
    ).json()
    picks = [body["suggestion"]["symbol"]] + [r["symbol"] for r in body["runners_up"]]
    assert "SALUD" not in picks


def test_no_suggestion_is_better_than_a_bad_one(client, portfolio_id, db_of):
    """Sin candidatos aceptables devuelve null con el motivo.

    Recomendar algo malo bajo un botón que promete «Sugerencia óptima» sería
    peor que no recomendar nada.
    """
    today = dt.date.today()
    for index in range(6):
        asset = Asset(symbol=f"MALA{index}", sector="Technology",
                      currency="USD", is_universe=True)
        db_of.add(asset)
        db_of.flush()
        price = 100.0
        for day in range(300):
            price *= 0.99 * (1.10 if day % 2 else 0.90)
            db_of.add(PriceHistory(asset_id=asset.id,
                                   date=today - dt.timedelta(days=300 - day),
                                   close=price, adj_close=price))
        db_of.add(FundamentalSnapshot(asset_id=asset.id, as_of=today,
                                      trailing_pe=120.0, return_on_equity=-0.20,
                                      profit_margin=-0.10, debt_to_equity=700.0,
                                      revenue_growth=-0.35,
                                      fetched_at=dt.datetime.now(UTC)))
    spy = Asset(symbol="SPY", asset_type=AssetType.ETF, currency="USD", is_universe=True)
    db_of.add(spy)
    db_of.flush()
    db_of.add(FundamentalSnapshot(asset_id=spy.id, as_of=today, trailing_pe=25.0,
                                  fetched_at=dt.datetime.now(UTC)))
    db_of.commit()

    body = client.get(
        f"/api/portfolios/{portfolio_id}/suggested-stock?refresh=false"
    ).json()

    assert body["suggestion"] is None
    assert body["no_suggestion_reason"]
    assert any("calificación absoluta" in w for w in body["warnings"])


def test_a_tiny_universe_is_rejected(client, portfolio_id):
    response = client.get(
        f"/api/portfolios/{portfolio_id}/suggested-stock?refresh=false"
    )
    assert response.status_code == 422
    assert "Sincroniza" in response.json()["detail"]
