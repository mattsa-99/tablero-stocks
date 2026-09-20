"""Tests del refresco que corre fuera de la petición.

Lo que se protege aquí es que la vista nunca espere a la red y que abrirla
varias veces no multiplique las llamadas a Yahoo.
"""

from __future__ import annotations

import threading

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.registry import Base
from app.models import Asset
from app.providers.cache import flight_lock
from app.services import refresh_jobs
from tests.fakes import FakeProvider, quote


def factory_with(tmp_path, symbols):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}", future=True)
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, expire_on_commit=False)
    session = maker()
    for symbol in symbols:
        session.add(Asset(symbol=symbol, currency="USD"))
    session.commit()
    session.close()
    return maker


def test_it_refreshes_the_symbols_it_is_given(tmp_path):
    maker = factory_with(tmp_path, ["AAPL", "MSFT"])
    provider = FakeProvider(
        quotes={"AAPL": quote("AAPL", 230.0), "MSFT": quote("MSFT", 410.0)}
    )

    refresh_jobs.refresh_quotes_for(
        maker, provider, ["AAPL", "MSFT"], "USD", scope="test"
    )

    assert provider.call_count("fetch_quotes") == 1


def test_it_only_asks_for_quotes_and_fx_never_the_weekly_job(tmp_path):
    """Es la razón de ser del módulo, no un detalle de implementación.

    Lo único que cambia intradía y mueve el ranking es el precio. Las barras
    diarias son cierres inmutables, los fundamentales son trimestrales y los
    metadatos tienen TTL de 30 días: pedirlos al abrir una página no mejora
    ningún número y es lo que agotaba el presupuesto de peticiones.
    """
    maker = factory_with(tmp_path, ["AAPL"])
    provider = FakeProvider(quotes={"AAPL": quote("AAPL", 230.0)})

    refresh_jobs.refresh_quotes_for(maker, provider, ["AAPL"], "USD", scope="test")

    assert provider.call_count("fetch_history") == 0
    assert provider.call_count("fetch_fundamentals") == 0
    assert provider.call_count("fetch_metadata") == 0


def test_two_refreshes_at_once_only_call_yahoo_once(tmp_path):
    """Abrir la vista cinco veces seguidas no son cinco sincronizaciones."""
    maker = factory_with(tmp_path, ["AAPL"])
    provider = FakeProvider(quotes={"AAPL": quote("AAPL", 230.0)})

    started = threading.Event()
    release = threading.Event()
    original = provider.fetch_quotes

    def slow(symbols):
        started.set()
        release.wait(timeout=5)
        return original(symbols)

    provider.fetch_quotes = slow

    first = threading.Thread(
        target=refresh_jobs.refresh_quotes_for,
        args=(maker, provider, ["AAPL"], "USD"),
        kwargs={"scope": "compartido"},
    )
    first.start()
    started.wait(timeout=5)

    assert refresh_jobs.refresh_running("compartido") is True

    # El segundo llega con el primero aún dentro: debe rendirse, no encolarse.
    refresh_jobs.refresh_quotes_for(maker, provider, ["AAPL"], "USD", scope="compartido")

    release.set()
    first.join(timeout=5)

    assert provider.call_count("fetch_quotes") == 1
    assert refresh_jobs.refresh_running("compartido") is False


def test_a_failure_does_not_escape(tmp_path):
    """Esto corre DESPUÉS de responder: no hay a quién devolverle el error.

    Si la excepción escapara, moriría el hilo del worker por un fallo que el
    usuario ya no puede ver y que el siguiente intento reintentaría solo.
    """
    maker = factory_with(tmp_path, ["AAPL"])

    class Exploding(FakeProvider):
        def fetch_quotes(self, symbols):
            raise RuntimeError("boom")

    refresh_jobs.refresh_quotes_for(
        maker, Exploding(), ["AAPL"], "USD", scope="fallo"
    )

    assert refresh_jobs.refresh_running("fallo") is False, "El candado se suelta igual"


def test_the_lock_is_released_even_when_nothing_matches(tmp_path):
    """Un `return` temprano dentro del `try` no puede dejarse el candado."""
    maker = factory_with(tmp_path, ["AAPL"])

    refresh_jobs.refresh_quotes_for(
        maker, FakeProvider(), ["NO_EXISTE"], "USD", scope="vacio"
    )

    assert flight_lock("background_refresh", "vacio").locked() is False
