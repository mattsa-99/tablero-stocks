"""Tests del pipeline de ingesta: universo, sincronización y almacenamiento."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select, text

from app.models import Asset, Portfolio, PriceHistory
from app.models.sync import SyncRun, SyncStatus, SyncTrigger
from app.services import ingestion
from app.services import universe as universe_service
from tests.fakes import FakeProvider, metadata, quote, synthetic_series

UTC = dt.UTC


# --------------------------------------------------------------------------
# Universo
# --------------------------------------------------------------------------


def test_seed_is_idempotent(db):
    first = universe_service.seed_universe(db, ["AAPL", "MSFT", "SPY"])
    second = universe_service.seed_universe(db, ["AAPL", "MSFT", "SPY"])

    assert first == 3
    assert second == 0, "Sembrar dos veces no debe duplicar nada"
    assert db.scalar(select(func.count()).select_from(Asset)) == 3


def test_seed_promotes_an_existing_asset(db):
    db.add(Asset(symbol="AAPL", currency="USD"))
    db.commit()

    assert universe_service.seed_universe(db, ["AAPL"]) == 1
    assert db.scalar(select(Asset).where(Asset.symbol == "AAPL")).is_universe is True


def test_held_assets_join_the_universe_without_being_seeded(db, portfolio, asset):
    """Comprar algo fuera de la lista no puede dejarlo sin precio."""
    from decimal import Decimal

    from app.models import Transaction, TransactionType

    db.add(
        Transaction(
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            type=TransactionType.BUY,
            executed_at=dt.datetime.now(UTC),
            quantity=Decimal("1"),
            price=Decimal("100"),
            currency="USD",
            fx_rate_to_base=Decimal("4000"),
        )
    )
    db.commit()

    assert asset.is_universe is False
    symbols = {a.symbol for a in universe_service.get_ingestion_universe(db)}
    assert asset.symbol in symbols


def test_removing_from_universe_keeps_the_asset(db):
    universe_service.seed_universe(db, ["AAPL"])
    universe_service.set_membership(db, "AAPL", False)

    stored = db.scalar(select(Asset).where(Asset.symbol == "AAPL"))
    assert stored is not None, "Retirar del universo NO puede borrar el activo"
    assert stored.is_universe is False
    assert universe_service.get_ingestion_universe(db) == []


def test_default_universe_covers_the_requested_asset_types():
    symbols = {symbol for symbol, _ in universe_service.DEFAULT_UNIVERSE}
    assert {"SPY", "QQQ"} <= symbols, "ETFs de índice"
    assert "GC=F" in symbols, "Oro vía futuro continuo"
    assert "AAPL" in symbols


# --------------------------------------------------------------------------
# Ejecución de la sincronización
# --------------------------------------------------------------------------


def make_provider(symbols: list[str]) -> FakeProvider:
    return FakeProvider(
        quotes={s: quote(s, 100.0, 99.0) for s in symbols},
        history={s: synthetic_series(100.0, 300) for s in symbols},
        metadata={s: metadata(s, "Technology") for s in symbols},
        fundamentals={},
        fx={("USD", "COP"): 4150.0},
    )


def test_sync_records_a_run(db):
    universe_service.seed_universe(db, ["AAA", "BBB"])
    run = ingestion.run_sync(db, make_provider(["AAA", "BBB"]), trigger=SyncTrigger.MANUAL)

    assert run.id is not None
    assert run.status == SyncStatus.SUCCESS
    assert run.trigger == SyncTrigger.MANUAL
    assert run.symbols_requested == 2
    assert run.quotes_updated == 2
    assert run.bars_written == 600  # 2 símbolos x 300 barras
    assert run.finished_at is not None
    assert run.duration_seconds >= 0


def test_partial_failure_is_not_reported_as_total(db):
    """Si parte del universo sí se actualizó, presentarlo como fallo total
    llevaría a descartar datos buenos."""
    universe_service.seed_universe(db, ["AAA", "MISSING"])
    run = ingestion.run_sync(db, make_provider(["AAA"]))

    assert run.status == SyncStatus.PARTIAL
    assert run.symbols_failed == 1
    assert run.quotes_updated == 1
    assert "MISSING" in (run.warnings or "")


def test_all_symbols_failing_is_a_failure(db):
    universe_service.seed_universe(db, ["AAA", "BBB"])
    run = ingestion.run_sync(db, FakeProvider())

    assert run.status == SyncStatus.FAILED
    assert run.symbols_failed == 2


def test_empty_universe_succeeds_quietly(db):
    run = ingestion.run_sync(db, FakeProvider())
    assert run.status == SyncStatus.SUCCESS
    assert run.symbols_requested == 0
    assert "vacío" in run.warnings


def test_sync_is_due_when_never_run(db):
    assert ingestion.is_sync_due(db) is True


def test_sync_not_due_right_after_a_run(db):
    universe_service.seed_universe(db, ["AAA"])
    ingestion.run_sync(db, make_provider(["AAA"]))
    assert ingestion.is_sync_due(db) is False


def test_sync_due_again_after_the_catchup_window(db):
    """Un portátil apagado a las 18:00 ET nunca vería el cron: por eso hay
    recuperación al arrancar, basada en el registro persistente."""
    universe_service.seed_universe(db, ["AAA"])
    run = ingestion.run_sync(db, make_provider(["AAA"]))

    run.finished_at = dt.datetime.now(UTC) - dt.timedelta(hours=48)
    db.commit()

    assert ingestion.is_sync_due(db) is True


def test_a_crashed_run_leaves_a_trace(db):
    """El SyncRun se persiste como RUNNING antes de empezar.

    Si el proceso muere a mitad queda constancia del intento, en lugar de un
    silencio que parece «nunca se sincronizó».
    """
    universe_service.seed_universe(db, ["AAA"])

    class Exploding(FakeProvider):
        def fetch_metadata(self, symbols):
            raise RuntimeError("boom")

    run = ingestion.run_sync(db, Exploding())
    assert run.status == SyncStatus.FAILED
    assert "boom" in run.warnings
    assert db.scalar(select(func.count()).select_from(SyncRun)) == 1


def test_sync_can_target_specific_symbols(db):
    universe_service.seed_universe(db, ["AAA", "BBB", "CCC"])
    run = ingestion.run_sync(db, make_provider(["AAA", "BBB", "CCC"]), symbols=["AAA"])

    assert run.symbols_requested == 1
    assert run.quotes_updated == 1


# --------------------------------------------------------------------------
# Refresco intradía (franjas de jornada, sin barras ni fundamentales)
# --------------------------------------------------------------------------


def test_intraday_universe_refresh_touches_only_quotes_and_fx(db):
    """Cotizaciones y tipos de cambio sí; barras y fundamentales no.

    La cartera se crea en COP EXPLÍCITAMENTE, y no se deja a la divisa por
    defecto: los activos sintéticos cotizan en USD, así que el par que se
    espera refrescar solo existe si la divisa base es otra. Dejándolo al valor
    por defecto, el test medía la configuración del desarrollador -pasaba con
    un `.env` que ponía COP y fallaba en un checkout limpio- en vez de medir
    el pipeline.
    """
    db.add(Portfolio(name="Base COP", base_currency="COP"))
    universe_service.seed_universe(db, ["AAA", "BBB"])
    before = db.scalar(select(func.count()).select_from(PriceHistory))

    report = ingestion.run_intraday_refresh(
        db, make_provider(["AAA", "BBB"]), scope="universe"
    )

    assert report.quotes_updated == 2
    assert report.fx_updated == 1, "USD->COP tenía que refrescarse"
    assert report.bars_written == 0
    assert report.fundamentals_updated == 0
    assert db.scalar(select(func.count()).select_from(PriceHistory)) == before


def test_intraday_refresh_does_not_record_a_sync_run(db):
    """El indicador de «última sincronización» solo lo mueve el job completo."""
    universe_service.seed_universe(db, ["AAA"])

    ingestion.run_intraday_refresh(db, make_provider(["AAA"]), scope="universe")

    assert db.scalar(select(func.count()).select_from(SyncRun)) == 0
    assert ingestion.is_sync_due(db) is True


def test_intraday_positions_scope_ignores_the_declared_universe(db, portfolio, asset):
    from decimal import Decimal

    from app.models import Transaction, TransactionType

    universe_service.seed_universe(db, ["AAA", "BBB"])  # miembros declarados
    db.add(
        Transaction(
            portfolio_id=portfolio.id,
            asset_id=asset.id,
            type=TransactionType.BUY,
            executed_at=dt.datetime.now(UTC),
            quantity=Decimal("1"),
            price=Decimal("100"),
            currency="USD",
            fx_rate_to_base=Decimal("4000"),
        )
    )
    db.commit()

    report = ingestion.run_intraday_refresh(
        db, make_provider(["AAA", "BBB", asset.symbol]), scope="positions"
    )

    assert report.quotes_updated == 1, "solo el activo con posición, no el universo"


# --------------------------------------------------------------------------
# Almacenamiento del histórico
# --------------------------------------------------------------------------


def test_bulk_insert_ignores_duplicates(db):
    """La reinserción del mismo rango no duplica ni revienta."""
    universe_service.seed_universe(db, ["AAA"])
    provider = make_provider(["AAA"])

    ingestion.run_sync(db, provider)
    total = db.scalar(select(func.count()).select_from(PriceHistory))

    ingestion.run_sync(db, provider, force=True)
    assert db.scalar(select(func.count()).select_from(PriceHistory)) == total


def test_price_history_is_without_rowid(db):
    ddl = db.execute(
        text("SELECT sql FROM sqlite_master WHERE name = 'price_history'")
    ).scalar_one()
    assert "WITHOUT ROWID" in ddl.upper()


def test_storage_stats_report_growth(db):
    universe_service.seed_universe(db, ["AAA"])
    ingestion.run_sync(db, make_provider(["AAA"]))

    stats = ingestion.storage_stats(db)
    assert stats["bars"] == 300
    assert stats["assets_with_history"] == 1
    assert stats["oldest_bar"] < stats["newest_bar"]
    assert stats["estimated_bytes"] > 0


def test_pruning_deletes_only_old_bars(db):
    universe_service.seed_universe(db, ["AAA"])
    ingestion.run_sync(db, make_provider(["AAA"]))
    before = db.scalar(select(func.count()).select_from(PriceHistory))

    deleted = ingestion.prune_history(db, retention_days=100)
    after = db.scalar(select(func.count()).select_from(PriceHistory))

    assert deleted > 0
    assert after == before - deleted
    oldest = db.scalar(select(func.min(PriceHistory.date)))
    assert oldest >= dt.date.today() - dt.timedelta(days=101)


def test_bulk_price_query_matches_the_per_asset_one(db):
    """La consulta masiva sustituye al N+1: deben dar exactamente lo mismo."""
    from app.repositories import market as market_repo

    universe_service.seed_universe(db, ["AAA", "BBB"])
    ingestion.run_sync(db, make_provider(["AAA", "BBB"]))

    assets = universe_service.get_ingestion_universe(db)
    bulk = market_repo.get_price_series_bulk(db, [a.id for a in assets], days=400)

    for asset in assets:
        one_by_one = [
            row.adj_close if row.adj_close is not None else row.close
            for row in market_repo.get_price_series(db, asset.id, days=400)
        ]
        assert bulk[asset.id] == one_by_one


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------


def test_sync_endpoint_returns_the_run(client):
    response = client.post("/api/market-data/sync", json={"symbols": ["CHEAP", "SOLID"]})
    assert response.status_code == 200

    body = response.json()
    assert body["trigger"] == "MANUAL"
    assert body["status"] in {"SUCCESS", "PARTIAL"}
    assert body["finished_at"] is not None


def test_status_endpoint_before_any_sync(client):
    body = client.get("/api/market-data/status").json()
    assert body["last_run"] is None
    assert body["is_due"] is True
    assert "storage" in body
    assert body["schedule"]


def test_status_endpoint_after_a_sync(client):
    client.post("/api/market-data/universe/CHEAP")
    client.post("/api/market-data/sync")

    body = client.get("/api/market-data/status").json()
    assert body["last_run"]["status"] in {"SUCCESS", "PARTIAL"}
    assert body["last_successful_run"] is not None
    assert body["is_due"] is False
    assert body["universe_size"] >= 1


def test_universe_endpoints_add_and_remove(client):
    added = client.post("/api/market-data/universe/NVDA")
    assert added.status_code == 200
    assert added.json()["symbol"] == "NVDA"

    listed = {a["symbol"] for a in client.get("/api/market-data/universe").json()}
    assert "NVDA" in listed

    removed = client.delete("/api/market-data/universe/NVDA")
    assert removed.status_code == 200
    assert "NVDA" not in {
        a["symbol"] for a in client.get("/api/market-data/universe").json()
    }


def test_prune_endpoint_refuses_a_reckless_retention(client):
    """Menos de 400 días rompería la SMA200 y el momentum 12-1."""
    assert client.post("/api/market-data/prune?retention_days=30").status_code == 422


@pytest.mark.parametrize("path", ["/api/market-data/sync", "/api/market-data/status"])
def test_endpoints_are_in_the_schema(client, path):
    assert path in client.get("/openapi.json").json()["paths"]


def test_the_sync_never_reaches_banrep_in_tests(db, monkeypatch):
    """Banrep es una fuente de red DISTINTA de Yahoo y `FakeProvider` no la cubre.

    Sin el interruptor, `run_sync` salía a internet de verdad y la suite se
    colgaba. El test no comprueba la configuración: comprueba que el gancho la
    respeta, que es lo que evita que vuelva a pasar si alguien cambia el
    defecto.
    """
    from app.core.config import settings
    from app.services import reference_rates as rates_service

    llamadas = []

    def espia(*args, **kwargs):
        llamadas.append(1)
        raise AssertionError("Ningún test puede salir a Banrep")

    monkeypatch.setattr(rates_service, "refresh", espia)

    monkeypatch.setattr(settings, "enable_reference_rates", False)
    ingestion.run_sync(db, FakeProvider(), force=True)
    assert llamadas == []

    # Y con el interruptor puesto SÍ se llama: si no, el test anterior pasaría
    # aunque el gancho no existiera.
    monkeypatch.setattr(settings, "enable_reference_rates", True)
    with pytest.raises(AssertionError, match="Banrep"):
        rates_service.refresh(db)
