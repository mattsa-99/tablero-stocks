"""Tests de la capa de mercado: caché, TTL, backoff y degradación.

Ninguno toca la red: usan `FakeProvider`, lo que permite verificar que el TTL
EVITA llamadas, cosa imposible de comprobar con red real.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import select

from app.models import Asset, AssetQuote, DataSyncState, FxRateDaily, PriceHistory
from app.providers.cache import ResourceType, SyncGate
from app.services.market_data import MarketDataService, fx_sanity_check
from tests.fakes import (
    RATE_LIMIT,
    FakeProvider,
    fundamentals,
    metadata,
    quote,
    synthetic_series,
)


def make_asset(db, symbol="AAPL", currency="USD", sector="Technology") -> Asset:
    asset = Asset(symbol=symbol, currency=currency, sector=sector)
    db.add(asset)
    db.commit()
    return asset


# --------------------------------------------------------------------------
# TTL y backoff
# --------------------------------------------------------------------------


def test_ttl_prevents_a_second_call(db):
    asset = make_asset(db)
    provider = FakeProvider(quotes={"AAPL": quote("AAPL", 230.0)})
    service = MarketDataService(db, provider)

    service.refresh_quotes([asset])
    service.refresh_quotes([asset])

    assert provider.call_count("fetch_quotes") == 1, "El TTL debe evitar la 2ª llamada"


def test_force_bypasses_ttl(db):
    asset = make_asset(db)
    provider = FakeProvider(quotes={"AAPL": quote("AAPL", 230.0)})
    service = MarketDataService(db, provider)

    service.refresh_quotes([asset])
    service.refresh_quotes([asset], force=True)

    assert provider.call_count("fetch_quotes") == 2


def test_force_does_not_bypass_backoff(db):
    """Forzar no puede convertirse en martilleo contra un endpoint que ya falla."""
    asset = make_asset(db)
    provider = FakeProvider(fail_with=RATE_LIMIT)
    service = MarketDataService(db, provider)

    service.refresh_quotes([asset])
    assert provider.call_count("fetch_quotes") == 1

    service.refresh_quotes([asset], force=True)
    assert provider.call_count("fetch_quotes") == 1, "El backoff debe seguir en pie"


def test_backoff_grows_with_consecutive_failures(db):
    gate = SyncGate(db)
    for _ in range(3):
        gate.mark_failure(ResourceType.QUOTE, "AAPL", 900, RuntimeError("boom"))
    db.commit()

    state = db.scalar(select(DataSyncState))
    assert state.consecutive_failures == 3
    # 60 * 2^2 = 240s de base, más jitter de hasta el 25%
    delay = (state.next_eligible_at - state.last_attempt_at).total_seconds()
    assert 240 <= delay <= 300


def test_success_resets_the_failure_counter(db):
    gate = SyncGate(db)
    gate.mark_failure(ResourceType.QUOTE, "AAPL", 900, RuntimeError("boom"))
    gate.mark_success(ResourceType.QUOTE, "AAPL", 900)
    db.commit()

    state = db.scalar(select(DataSyncState))
    assert state.consecutive_failures == 0
    assert state.next_eligible_at is None
    assert state.last_error is None


# --------------------------------------------------------------------------
# Degradación ante fallo
# --------------------------------------------------------------------------


def test_provider_failure_marks_stale_and_keeps_last_price(db):
    """Un fallo de Yahoo no puede dejar el dashboard en blanco."""
    asset = make_asset(db)
    working = FakeProvider(quotes={"AAPL": quote("AAPL", 230.0)})
    MarketDataService(db, working).refresh_quotes([asset])

    broken = FakeProvider(fail_with=RATE_LIMIT)
    report = MarketDataService(db, broken).refresh_quotes([asset], force=True)

    stored = db.get(AssetQuote, asset.id)
    assert stored.price == 230.0, "Se conserva el último precio conocido"
    assert stored.is_stale is True
    assert report.warnings


def test_missing_symbol_in_batch_does_not_break_others(db):
    good = make_asset(db, "AAPL")
    bad = make_asset(db, "FAKEXYZ")
    provider = FakeProvider(quotes={"AAPL": quote("AAPL", 230.0)})

    report = MarketDataService(db, provider).refresh_quotes([good, bad])

    assert report.quotes_updated == 1
    assert "FAKEXYZ" in report.failed_symbols
    assert db.get(AssetQuote, good.id) is not None


# --------------------------------------------------------------------------
# Histórico incremental
# --------------------------------------------------------------------------


def test_history_requests_only_the_gap(db):
    """Las barras cerradas son inmutables: solo se piden las ausentes."""
    asset = make_asset(db)
    # La serie termina hace 10 días, así que queda un hueco real que pedir.
    bars = synthetic_series(100.0, 30, end=dt.date.today() - dt.timedelta(days=10))
    provider = FakeProvider(history={"AAPL": bars})
    service = MarketDataService(db, provider)

    service.refresh_price_history([asset])
    assert len(db.scalars(select(PriceHistory)).all()) == 30

    service.refresh_price_history([asset], force=True)
    assert len(db.scalars(select(PriceHistory)).all()) == 30, "No debe duplicar barras"

    history_calls = [c for c in provider.calls if c[0] == "fetch_history"]
    assert len(history_calls) == 2
    first_start = history_calls[0][1][1]
    second_start = history_calls[1][1][1]

    assert first_start < bars[0].date, "El primer fetch cubre toda la ventana"
    assert second_start == bars[-1].date + dt.timedelta(days=1), (
        "El segundo debe arrancar justo tras la última barra almacenada"
    )


def test_history_skips_entirely_when_up_to_date(db):
    """Si la última barra es de hoy no hay nada que pedir: ni una llamada."""
    asset = make_asset(db)
    provider = FakeProvider(history={"AAPL": synthetic_series(100.0, 30)})
    service = MarketDataService(db, provider)

    service.refresh_price_history([asset])
    service.refresh_price_history([asset], force=True)

    assert provider.call_count("fetch_history") == 1


# --------------------------------------------------------------------------
# Divisas
# --------------------------------------------------------------------------


def test_fx_sanity_rejects_inverted_pair():
    """USD/COP invertido vale ~0.00025 en vez de ~4000: 7 órdenes de magnitud."""
    assert fx_sanity_check("USD", "COP", 4000.0) is None
    assert fx_sanity_check("USD", "COP", 0.00025) is not None
    assert fx_sanity_check("USD", "COP", -1.0) is not None


def test_inverted_fx_is_not_persisted(db):
    """Un FX invertido corrompe el valor de TODA la cartera, no de una posición."""
    provider = FakeProvider(fx={("USD", "COP"): 0.00025})
    report = MarketDataService(db, provider).refresh_fx([("USD", "COP")])

    assert db.scalar(select(FxRateDaily)) is None
    assert any("invertido" in w for w in report.warnings)


def test_valid_fx_is_persisted(db):
    provider = FakeProvider(fx={("USD", "COP"): 4150.0})
    report = MarketDataService(db, provider).refresh_fx([("USD", "COP")])

    stored = db.scalar(select(FxRateDaily))
    assert stored.rate == Decimal("4150.0")
    assert report.fx_updated == 1


# --------------------------------------------------------------------------
# Metadatos y fundamentales
# --------------------------------------------------------------------------


def test_metadata_enriches_the_asset(db):
    asset = make_asset(db, "AAPL", sector=None)
    provider = FakeProvider(metadata={"AAPL": metadata("AAPL", "Technology")})

    MarketDataService(db, provider).refresh_metadata([asset])
    db.refresh(asset)

    assert asset.sector == "Technology"
    assert asset.name == "AAPL Inc."
    assert asset.last_verified_at is not None


def test_unverifiable_ticker_is_deactivated_not_deleted(db):
    """Puede tener transacciones: se desactiva para dejar de gastar llamadas."""
    asset = make_asset(db, "FAKEXYZ")
    provider = FakeProvider(metadata={})

    report = MarketDataService(db, provider).refresh_metadata([asset])
    db.refresh(asset)

    assert asset.is_active is False
    assert db.scalar(select(Asset).where(Asset.symbol == "FAKEXYZ")) is not None
    assert "FAKEXYZ" in report.failed_symbols


def test_fundamentals_are_idempotent_within_a_day(db):
    asset = make_asset(db)
    provider = FakeProvider(
        fundamentals={"AAPL": fundamentals("AAPL", trailing_pe=28.0, beta=1.2)}
    )
    service = MarketDataService(db, provider)

    service.refresh_fundamentals([asset])
    service.refresh_fundamentals([asset], force=True)
    db.refresh(asset)

    assert len(asset.fundamentals) == 1, "Un snapshot por activo y día"
    assert asset.fundamentals[0].trailing_pe == 28.0


def test_full_refresh_touches_every_resource(db):
    asset = make_asset(db)
    provider = FakeProvider(
        quotes={"AAPL": quote("AAPL", 230.0)},
        history={"AAPL": synthetic_series(100.0, 30)},
        metadata={"AAPL": metadata("AAPL", "Technology")},
        fundamentals={"AAPL": fundamentals("AAPL", trailing_pe=28.0)},
        fx={("USD", "COP"): 4150.0},
    )
    report = MarketDataService(db, provider).full_refresh([asset], ["COP"])

    assert report.quotes_updated == 1
    assert report.bars_written == 30
    assert report.fundamentals_updated == 1
    assert report.fx_updated == 1
    assert report.metadata_updated == 1
