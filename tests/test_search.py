"""Tests del autocompletado de símbolos."""

from __future__ import annotations

import pytest

from app.models import Asset
from app.providers.base import AssetMetadata
from app.services import search as search_service
from tests.fakes import RATE_LIMIT, FakeProvider, hit


@pytest.fixture(autouse=True)
def _clean_cache():
    """La caché de búsqueda es un singleton de proceso.

    Sin vaciarla, un test contaminaría al siguiente y los conteos de llamadas
    al proveedor darían resultados distintos según el orden de ejecución.
    """
    search_service.clear_cache()
    yield
    search_service.clear_cache()


def provider_with_hits() -> FakeProvider:
    return FakeProvider(
        search=[
            hit("NVDA", "NVIDIA Corporation"),
            hit("NVD.SG", "NVIDIA Corp", exchange="Stuttgart"),
            hit("NVHE-U.TO", "Harvest NVIDIA Enhanced ETF", exchange="Toronto", quote_type="ETF"),
            hit("AAPL", "Apple Inc."),
        ],
        metadata={
            "NVDA": AssetMetadata(
                symbol="NVDA", name="NVIDIA Corporation", currency="USD",
                exchange="NMS", sector="Technology", asset_type="EQUITY",
            )
        },
    )


# --------------------------------------------------------------------------
# Servicio
# --------------------------------------------------------------------------


def test_partial_name_finds_the_symbol(db):
    """El caso del enunciado: escribir «nvi» debe encontrar NVDA."""
    results, warnings = search_service.search_symbols(db, provider_with_hits(), "nvi")

    assert warnings == []
    assert results[0].symbol == "NVDA"
    assert results[0].name == "NVIDIA Corporation"
    assert results[0].exchange == "NASDAQ"


def test_query_shorter_than_two_chars_returns_nothing(db):
    """Una letra da ruido y cuesta una llamada por pulsación."""
    provider = provider_with_hits()
    results, _ = search_service.search_symbols(db, provider, "n")

    assert results == []
    assert provider.call_count("search_symbols") == 0


def test_local_catalog_comes_first(db):
    """Lo que ya sigues es más probable que lo que Yahoo puntúe mejor."""
    db.add(Asset(symbol="NVDA", name="NVIDIA Corporation", currency="USD",
                 sector="Technology", is_universe=True))
    db.commit()

    results, _ = search_service.search_symbols(db, provider_with_hits(), "nvi")

    assert results[0].symbol == "NVDA"
    assert results[0].source == "catalog"
    assert results[0].in_universe is True
    # El catálogo sí conoce divisa y sector; Yahoo no los da al buscar.
    assert results[0].currency == "USD"
    assert results[0].sector == "Technology"


def test_no_duplicates_between_sources(db):
    db.add(Asset(symbol="NVDA", name="NVIDIA Corporation", currency="USD"))
    db.commit()

    results, _ = search_service.search_symbols(db, provider_with_hits(), "nvi")
    symbols = [r.symbol for r in results]

    assert symbols.count("NVDA") == 1


def test_search_matches_by_name_in_the_catalog(db):
    """Buscar por nombre es el punto de la feature, no solo por ticker."""
    db.add(Asset(symbol="XYZ1", name="Compañía Nacional de Chocolates", currency="COP"))
    db.commit()

    results, _ = search_service.search_symbols(db, FakeProvider(), "chocolat")
    assert [r.symbol for r in results] == ["XYZ1"]


def test_prefix_matches_rank_above_substring(db):
    """Escribir «AA» debe sugerir AAPL antes que BRKAA."""
    db.add_all([
        Asset(symbol="BRKAA", name="Berkshire clase AA", currency="USD"),
        Asset(symbol="AAPL", name="Apple Inc.", currency="USD"),
    ])
    db.commit()

    results, _ = search_service.search_symbols(db, FakeProvider(), "AA")
    assert results[0].symbol == "AAPL"


def test_held_assets_are_flagged(db, portfolio, asset):
    """Saber que ya lo tienes cambia la operación que vas a registrar."""
    import datetime as dt
    from decimal import Decimal

    from app.models import Transaction, TransactionType

    db.add(
        Transaction(
            portfolio_id=portfolio.id, asset_id=asset.id, type=TransactionType.BUY,
            executed_at=dt.datetime.now(dt.UTC), quantity=Decimal("1"),
            price=Decimal("100"), currency="USD", fx_rate_to_base=Decimal("4000"),
        )
    )
    db.commit()

    results, _ = search_service.search_symbols(db, FakeProvider(), asset.symbol[:3])
    assert results[0].is_held is True


def test_provider_failure_degrades_to_local_results(db):
    """Un autocompletado que se cae al perder la red es peor que uno parcial."""
    db.add(Asset(symbol="NVDA", name="NVIDIA Corporation", currency="USD"))
    db.commit()

    results, warnings = search_service.search_symbols(
        db, FakeProvider(fail_with=RATE_LIMIT), "nvi"
    )

    assert [r.symbol for r in results] == ["NVDA"]
    assert warnings and "Yahoo" in warnings[0]


def test_provider_failure_with_empty_catalog_returns_nothing_not_an_error(db):
    results, warnings = search_service.search_symbols(
        db, FakeProvider(fail_with=RATE_LIMIT), "nvi"
    )
    assert results == []
    assert warnings


def test_cache_avoids_a_second_provider_call(db):
    """Escribir «nvidia» dispara una consulta por pulsación; la caché las colapsa."""
    provider = provider_with_hits()

    search_service.search_symbols(db, provider, "nvi")
    search_service.search_symbols(db, provider, "nvi")
    search_service.search_symbols(db, provider, "NVI")  # insensible a mayúsculas

    assert provider.call_count("search_symbols") == 1


def test_local_results_filling_the_limit_skip_the_network(db):
    """Si el catálogo ya cubre el límite, no se molesta al proveedor."""
    db.add_all([
        Asset(symbol=f"NVI{i}", name=f"Nvidia clon {i}", currency="USD") for i in range(8)
    ])
    db.commit()
    provider = provider_with_hits()

    results, _ = search_service.search_symbols(db, provider, "nvi", limit=8)

    assert len(results) == 8
    assert provider.call_count("search_symbols") == 0


def test_limit_is_respected(db):
    results, _ = search_service.search_symbols(db, provider_with_hits(), "nvi", limit=2)
    assert len(results) == 2


# --------------------------------------------------------------------------
# Resolución del símbolo elegido
# --------------------------------------------------------------------------


def test_resolve_fills_currency_and_sector(db):
    """Es lo que permite al formulario rellenarse solo."""
    resolved = search_service.resolve_symbol(db, provider_with_hits(), "NVDA")

    assert resolved.symbol == "NVDA"
    assert resolved.currency == "USD"
    assert resolved.sector == "Technology"


def test_resolve_prefers_the_catalog_and_skips_the_network(db):
    db.add(Asset(symbol="NVDA", name="NVIDIA", currency="USD", sector="Technology"))
    db.commit()
    provider = provider_with_hits()

    resolved = search_service.resolve_symbol(db, provider, "NVDA")

    assert resolved.source == "catalog"
    assert provider.call_count("fetch_metadata") == 0


def test_resolve_persists_nothing(db):
    """También la usa el simulador, que no puede escribir en la base."""
    from sqlalchemy import func, select

    before = db.scalar(select(func.count()).select_from(Asset))
    search_service.resolve_symbol(db, provider_with_hits(), "NVDA")
    assert db.scalar(select(func.count()).select_from(Asset)) == before


def test_resolve_unknown_symbol_returns_none(db):
    assert search_service.resolve_symbol(db, FakeProvider(), "NOEXISTE") is None


def test_resolve_falls_back_to_the_catalog_when_the_provider_fails(db):
    """Un activo sin sector es mejor respuesta que ninguna."""
    db.add(Asset(symbol="NVDA", name="NVIDIA", currency="USD", sector=None))
    db.commit()

    resolved = search_service.resolve_symbol(db, FakeProvider(fail_with=RATE_LIMIT), "NVDA")

    assert resolved is not None
    assert resolved.symbol == "NVDA"
    assert resolved.sector is None


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_search_endpoint(client):
    body = client.get("/api/market-data/search?q=CHE").json()

    assert body["query"] == "CHE"
    assert isinstance(body["results"], list)
    assert isinstance(body["warnings"], list)


def test_search_endpoint_rejects_an_empty_query(client):
    assert client.get("/api/market-data/search?q=").status_code == 422


def test_search_endpoint_caps_the_limit(client):
    assert client.get("/api/market-data/search?q=nvi&limit=99").status_code == 422


def test_resolve_endpoint_404s_on_an_unknown_symbol(client):
    response = client.get("/api/market-data/resolve/NOEXISTE")
    assert response.status_code == 422
    assert "NOEXISTE" in response.json()["detail"]


def test_suggestion_shape_matches_what_the_frontend_reads(client, db_of):
    """El combobox lee estos campos por nombre; renombrarlos lo rompe en silencio."""
    db_of.add(Asset(symbol="TESTSYM", name="Test Corp", currency="USD", sector="Tech"))
    db_of.commit()

    results = client.get("/api/market-data/search?q=TESTSYM").json()["results"]
    assert results, "El catálogo local debería devolver el símbolo sembrado"

    expected = {
        "symbol", "name", "exchange", "asset_type", "currency", "sector",
        "price", "price_as_of", "price_is_stale",
        "source", "in_catalog", "in_universe", "is_held",
    }
    assert set(results[0]) == expected


# --------------------------------------------------------------------------
# Precio de referencia
# --------------------------------------------------------------------------


def _cached_quote(db, asset: Asset, price: float, *, age_hours: float = 0.0, stale=False):
    import datetime as dt

    from app.models import AssetQuote

    moment = dt.datetime.now(dt.UTC) - dt.timedelta(hours=age_hours)
    db.add(
        AssetQuote(
            asset_id=asset.id, price=price, previous_close=price * 0.99,
            currency=asset.currency, quote_time=moment, fetched_at=moment,
            is_stale=stale,
        )
    )
    db.commit()


def test_resolve_uses_the_cached_price_without_network(db):
    """Lo más barato primero: si hay cotización fresca, no se sale a la red."""
    asset = Asset(symbol="NVDA", name="NVIDIA", currency="USD", sector="Technology")
    db.add(asset)
    db.commit()
    _cached_quote(db, asset, 138.79)
    provider = provider_with_hits()

    resolved = search_service.resolve_symbol(db, provider, "NVDA")

    assert resolved.price == 138.79
    assert resolved.price_is_stale is False
    assert provider.call_count("fetch_quotes") == 0


def test_resolve_refreshes_a_stale_price(db):
    """Un precio viejo es justo el que NO debe prellenar el formulario."""
    from tests.fakes import quote as make_quote

    asset = Asset(symbol="NVDA", name="NVIDIA", currency="USD", sector="Technology")
    db.add(asset)
    db.commit()
    _cached_quote(db, asset, 100.0, age_hours=72)

    provider = FakeProvider(quotes={"NVDA": make_quote("NVDA", 138.79, 137.0)})
    resolved = search_service.resolve_symbol(db, provider, "NVDA")

    assert resolved.price == 138.79, "Debe usarse el precio recién traído"
    assert resolved.price_is_stale is False
    assert provider.call_count("fetch_quotes") == 1


def test_resolve_keeps_the_stale_price_when_the_refresh_fails(db):
    """Un precio viejo MARCADO como viejo es mejor que ninguno."""
    asset = Asset(symbol="NVDA", name="NVIDIA", currency="USD", sector="Technology")
    db.add(asset)
    db.commit()
    _cached_quote(db, asset, 100.0, age_hours=72)

    resolved = search_service.resolve_symbol(db, FakeProvider(fail_with=RATE_LIMIT), "NVDA")

    assert resolved.price == 100.0
    assert resolved.price_is_stale is True


def test_resolve_fetches_the_price_for_an_unknown_symbol(db):
    from tests.fakes import quote as make_quote

    provider = provider_with_hits()
    provider.quotes = {"NVDA": make_quote("NVDA", 138.79)}

    resolved = search_service.resolve_symbol(db, provider, "NVDA")
    assert resolved.price == 138.79


def test_resolve_without_any_price_returns_none_not_zero(db):
    """Un precio inventado en el coste base queda congelado para siempre."""
    resolved = search_service.resolve_symbol(db, provider_with_hits(), "NVDA")
    assert resolved is not None
    assert resolved.price is None


def test_search_includes_cached_prices_without_network(db):
    asset = Asset(symbol="NVDA", name="NVIDIA Corporation", currency="USD")
    db.add(asset)
    db.commit()
    _cached_quote(db, asset, 138.79)
    provider = provider_with_hits()

    results, _ = search_service.search_symbols(db, provider, "nvi")

    assert results[0].price == 138.79
    assert provider.call_count("fetch_quotes") == 0


def test_provider_results_carry_no_invented_price(db):
    """Yahoo no da precio al buscar: ausencia no engaña, un número sí."""
    results, _ = search_service.search_symbols(db, provider_with_hits(), "nvi")
    remote = [r for r in results if r.source == "provider"]
    assert remote and all(r.price is None for r in remote)
