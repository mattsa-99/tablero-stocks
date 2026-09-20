from __future__ import annotations

import datetime as dt
import os
import tempfile
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

# Debe fijarse ANTES de importar cualquier módulo de app: app.db.session crea
# el engine en tiempo de import.
_TMP_DIR = tempfile.mkdtemp(prefix="tablero-tests-")
os.environ["TABLERO_DATABASE_URL"] = f"sqlite:///{Path(_TMP_DIR) / 'test.db'}"

# El planificador se apaga AQUÍ y no en el shell: un test cuyo resultado
# depende de una variable de entorno externa pasa en local y falla en CI. Con
# él activo, el lifespan del TestClient sembraría el universo y saldría a la
# red de verdad.
os.environ["TABLERO_ENABLE_BACKGROUND_REFRESH"] = "false"

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

import app.db.session  # noqa: E402, F401  -- registra el listener de PRAGMAs
from app.db.registry import Base  # noqa: E402
from app.models import Asset, Portfolio  # noqa: E402

UTC = dt.UTC


@pytest.fixture
def db() -> Iterator[Session]:
    """Base de datos limpia por test, en fichero (no :memory:).

    En fichero y no en memoria porque el listener de PRAGMAs actúa sobre cada
    conexión nueva, y queremos ejercitar exactamente el mismo camino que en
    producción, incluido PRAGMA foreign_keys=ON.
    """
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine(f"sqlite:///{Path(tmp) / 'unit.db'}", future=True)
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine, expire_on_commit=False)()
        try:
            yield session
        finally:
            session.close()
            engine.dispose()


@pytest.fixture
def portfolio(db: Session) -> Portfolio:
    p = Portfolio(name="Principal", base_currency="COP")
    db.add(p)
    db.commit()
    return p


@pytest.fixture
def asset(db: Session) -> Asset:
    a = Asset(symbol="AAPL", name="Apple Inc.", currency="USD", sector="Technology")
    db.add(a)
    db.commit()
    return a


@pytest.fixture
def now() -> dt.datetime:
    return dt.datetime.now(UTC).replace(microsecond=0)


def buy(portfolio, asset, when, qty="10", price="150", fees="0", fx="4000"):
    """Constructor de una compra con valores por defecto razonables."""
    from app.models import Transaction, TransactionType

    return Transaction(
        portfolio_id=portfolio.id,
        asset_id=asset.id,
        type=TransactionType.BUY,
        executed_at=when,
        quantity=Decimal(qty),
        price=Decimal(price),
        fees=Decimal(fees),
        currency="USD",
        fx_rate_to_base=Decimal(fx),
    )


# ---------------------------------------------------------------------------
# Fixtures de API. Viven aquí -y no en un módulo de test- para que pytest las
# resuelva por nombre en cualquier archivo, sin imports cruzados entre tests.
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.routers.dependencies import get_provider, get_session_factory  # noqa: E402
from tests.fakes import (  # noqa: E402
    FakeProvider,
    fundamentals,
    metadata,
    quote,
    synthetic_series,
)


def _universe() -> FakeProvider:
    """Universo sintético de 6 activos con perfiles deliberadamente distintos.

    Los parámetros están elegidos para que el ranking sea PREDECIBLE:
    CHEAP tiene el P/E más bajo y tendencia alcista; RISKY tiene la mayor
    volatilidad; EXPENSIVE el múltiplo más alto.
    """
    profiles = {
        "CHEAP": dict(pe=8.0, pb=0.8, drift=0.0020, amplitude=0.01, sector="Energy"),
        "SOLID": dict(pe=15.0, pb=1.5, drift=0.0012, amplitude=0.01, sector="Healthcare"),
        "FAIR": dict(pe=22.0, pb=2.5, drift=0.0006, amplitude=0.02, sector="Industrials"),
        "PRICEY": dict(pe=45.0, pb=6.0, drift=0.0008, amplitude=0.02, sector="Technology"),
        "EXPENSIVE": dict(pe=80.0, pb=12.0, drift=0.0002, amplitude=0.03, sector="Technology"),
        "RISKY": dict(pe=30.0, pb=4.0, drift=-0.0010, amplitude=0.09, sector="Technology"),
    }

    quotes, history, meta, funds = {}, {}, {}, {}
    for symbol, profile in profiles.items():
        bars = synthetic_series(
            100.0, 400, drift=profile["drift"], amplitude=profile["amplitude"]
        )
        history[symbol] = bars
        quotes[symbol] = quote(symbol, bars[-1].close, bars[-2].close)
        meta[symbol] = metadata(symbol, profile["sector"])
        funds[symbol] = fundamentals(
            symbol,
            trailing_pe=profile["pe"],
            forward_pe=profile["pe"] * 0.9,
            price_to_book=profile["pb"],
            beta=1.0 + profile["amplitude"] * 10,
        )

    return FakeProvider(
        quotes=quotes,
        history=history,
        metadata=meta,
        fundamentals=funds,
        fx={("USD", "COP"): 4150.0},
    )


@pytest.fixture
def provider() -> FakeProvider:
    return _universe()


@pytest.fixture
def api_engine(tmp_path):
    """Motor compartido por el cliente HTTP y la fixture `db_of`.

    Se extrae para que un test pueda inspeccionar la MISMA base que acaba de
    manipular por HTTP: es lo que permite contar filas antes y después de una
    simulación y demostrar que no escribió nada.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}", future=True)
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def api_session_factory(api_engine):
    return sessionmaker(bind=api_engine, expire_on_commit=False)


@pytest.fixture
def db_of(api_session_factory) -> Iterator[Session]:
    """Sesión directa sobre la base que usa el cliente HTTP."""
    session = api_session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(api_session_factory, provider) -> Iterator[TestClient]:
    def override_db() -> Iterator[Session]:
        session = api_session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_provider] = lambda: provider
    # Las tareas de fondo se abren su PROPIA sesión, porque la de la petición
    # ya está cerrada cuando arrancan. Sin sustituir también la fábrica,
    # escribirían en tablero.db -la base de verdad- mientras corre la suite.
    app.dependency_overrides[get_session_factory] = lambda: api_session_factory

    # El planificador se apaga: un bucle de fondo en los tests introduce
    # llamadas no deterministas y hace fallar aserciones de conteo.
    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture
def portfolio_id(client) -> int:
    response = client.post(
        "/api/portfolios", json={"name": "Principal", "base_currency": "COP"}
    )
    assert response.status_code == 201
    return response.json()["id"]
