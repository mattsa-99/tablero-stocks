"""Memoización del universo puntuado.

Filtrar es una operación de VISTA: el motor puntúa siempre el universo
completo y el filtro solo decide qué filas se enseñan. Estos tests fijan las
dos mitades de esa afirmación: que repetir la consulta NO vuelve a puntuar, y
que cualquier cosa capaz de mover un score SÍ lo hace.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.models import Asset, AssetQuote, AssetType, FundamentalSnapshot, Portfolio, PriceHistory
from app.services import opportunities as opportunity_service
from app.services.grading import Grade
from app.services.regions import MarketRegion
from tests.conftest import buy


@pytest.fixture
def universe(db):
    """Universo mínimo pero suficiente para un ranking con sentido."""
    today = dt.date.today()
    spec = [
        ("NVDA", "United States", 1.004, 15.0),
        ("KO", "United States", 1.003, 18.0),
        ("MSFT", "United States", 0.998, 80.0),
        ("CIB", "Colombia", 1.005, 9.0),
        ("PBR", "Brazil", 1.004, 8.0),
        ("SAP", "Germany", 1.002, 30.0),
    ]
    created: list[Asset] = []
    for symbol, country, drift, pe in spec:
        item = Asset(
            symbol=symbol, name=f"{symbol} SA", currency="USD",
            asset_type=AssetType.STOCK, country=country, sector="Energy",
        )
        db.add(item)
        db.flush()
        price = 100.0
        for day in range(300):
            price *= drift
            db.add(PriceHistory(
                asset_id=item.id, date=today - dt.timedelta(days=300 - day),
                close=price, adj_close=price,
            ))
        db.add(FundamentalSnapshot(
            asset_id=item.id, as_of=today, trailing_pe=pe,
            return_on_equity=0.18, profit_margin=0.12, debt_to_equity=60.0,
            revenue_growth=0.10, fetched_at=dt.datetime.now(dt.UTC),
        ))
        created.append(item)

    portfolio = Portfolio(name="Cacheado", base_currency="USD")
    db.add(portfolio)
    db.commit()
    return portfolio, created


@pytest.fixture
def counting(monkeypatch):
    """Cuenta cuántas veces se puntúa de verdad el universo."""
    calls = []
    original = opportunity_service._score_universe

    def spy(db, portfolio, assets):
        calls.append(1)
        return original(db, portfolio, assets)

    monkeypatch.setattr(opportunity_service, "_score_universe", spy)
    return calls


def run(db, universe, **kwargs):
    portfolio, assets = universe
    kwargs.setdefault("limit", 50)
    return opportunity_service.compute_opportunities(
        db, portfolio, assets=assets, **kwargs
    )


# ----------------------------------------------------------------------
# Lo que la caché debe ahorrar
# ----------------------------------------------------------------------


def test_a_filter_click_does_not_rescore_the_universe(db, universe, counting):
    """Es la razón de ser de la caché.

    Cada clic en un chip repetía el trabajo entero -186 ms medidos sobre los
    494 activos reales, 85 de ellos releyendo 135.851 barras- para producir
    exactamente las mismas filas. El filtro no toca ningún score, así que no
    tiene por qué costar ninguno.
    """
    run(db, universe)
    run(db, universe, regions={MarketRegion.COL})
    run(db, universe, quality_tiers={Grade.EXCELLENT})
    run(db, universe, limit=1)

    assert len(counting) == 1


def test_the_cached_ranking_is_identical_to_a_fresh_one(db, universe, counting):
    """Ahorrar trabajo no puede significar devolver algo distinto."""
    first = run(db, universe)
    opportunity_service.clear_cache()
    second = run(db, universe)

    assert len(counting) == 2
    assert [(r.symbol, r.rank, r.score) for r in first.opportunities] == [
        (r.symbol, r.rank, r.score) for r in second.opportunities
    ]


def test_filtering_never_mutates_the_cached_rows(db, universe):
    """Las filas se COMPARTEN entre peticiones: renumerarlas destrozaría la siguiente.

    `rank` es la posición en el ranking completo, y se asigna al puntuar. Si
    una petición filtrada lo tocara, la petición siguiente -servida desde la
    misma caché- vería los rangos del filtro anterior.
    """
    before = {r.symbol: r.rank for r in run(db, universe).opportunities}
    run(db, universe, regions={MarketRegion.COL})
    after = {r.symbol: r.rank for r in run(db, universe).opportunities}

    assert before == after


# ----------------------------------------------------------------------
# Lo que la caché NO puede ahorrar
# ----------------------------------------------------------------------


def test_a_new_quote_invalidates_the_cache(db, universe):
    """Las cotizaciones mueven el score, y por eso la huella no es la fecha de la barra.

    `fresh_trailing_pe` rehace el P/E con el precio de AHORA, así que una
    caché que solo mirase al histórico congelaría el ranking justo durante el
    refresco de fondo: exactamente cuando el usuario está esperando datos
    nuevos.
    """
    portfolio, assets = universe
    run(db, universe)

    db.add(AssetQuote(
        asset_id=assets[0].id, price=250.0, previous_close=240.0, currency="USD",
        quote_time=dt.datetime.now(dt.UTC), fetched_at=dt.datetime.now(dt.UTC),
        is_stale=False, source="test",
    ))
    db.commit()

    with_quote = run(db, universe)
    prices = {r.symbol: r.current_price for r in with_quote.opportunities}
    assert prices[assets[0].symbol] == 250.0


def test_a_new_transaction_invalidates_the_cache(db, universe, counting):
    """El ledger entra por el factor de diversificación.

    Los pesos por cubo salen de las posiciones abiertas: comprar cambia el
    peso del sector y con él la penalización de todo candidato que lo comparta.
    """
    portfolio, assets = universe
    run(db, universe)
    assert len(counting) == 1

    db.add(buy(portfolio, assets[0], dt.datetime.now(dt.UTC), fx="1"))
    db.commit()

    run(db, universe)
    assert len(counting) == 2


def test_changing_the_weights_invalidates_the_cache(db, universe, monkeypatch):
    """Los pesos son ajustables por entorno: la huella tiene que incluirlos."""
    from app.core.config import settings

    first = run(db, universe).opportunities[0].value.weight
    monkeypatch.setattr(settings, "opportunity_weight_value", 0.60)
    second = run(db, universe).opportunities[0].value.weight

    assert first != second
    # El peso que viaja en la respuesta es el EFECTIVO, ya reescalado a
    # [0, 100]: 0,60 sobre una suma de pesos de 1,10 es 0,5455. Comprobar el
    # valor crudo dejaría pasar un reescalado roto.
    esperado = 0.60 / (
        0.60
        + settings.opportunity_weight_momentum
        + settings.opportunity_weight_risk
    )
    assert second == pytest.approx(esperado, abs=1e-4)


def test_a_different_universe_is_a_different_entry(db, universe, counting):
    """Añadir un símbolo cambia los percentiles de todos los demás."""
    portfolio, assets = universe
    run(db, universe)

    extra = Asset(symbol="XOM", name="Exxon", currency="USD",
                  asset_type=AssetType.STOCK, country="United States")
    db.add(extra)
    db.commit()

    opportunity_service.compute_opportunities(
        db, portfolio, assets=[*assets, extra], limit=50
    )
    assert len(counting) == 2


# ----------------------------------------------------------------------
# El score ya no depende de la cartera
# ----------------------------------------------------------------------


def test_the_score_no_longer_depends_on_the_portfolio(db, universe):
    """LA propiedad del cambio: dos carteras, el mismo ranking.

    Antes el score incluía `+ 0.15*D`, donde D medía cuánto pesaba en TU
    cartera el cubo del candidato. Medido sobre el universo real evaluado
    desde dos carteras distintas:

        símbolos que cambian de puesto:  483 de 490  (98,6%)
        IVV: #220 (57,79) -> #113 (61,37)

    Es decir: el número que se lee como «qué tan buena es esta oportunidad»
    cambiaba con lo que uno tuviera comprado, y el 98,6% de los puestos con él.
    """
    portfolio, assets = universe

    otra = Portfolio(name="Con posiciones", base_currency="USD")
    db.add(otra)
    db.commit()
    db.add(buy(otra, assets[0], dt.datetime.now(dt.UTC) - dt.timedelta(days=5), fx="1"))
    db.commit()

    opportunity_service.clear_cache()
    vacia = {
        row.symbol: (row.rank, row.score)
        for row in opportunity_service.compute_opportunities(
            db, portfolio, assets=assets, limit=50
        ).opportunities
    }
    opportunity_service.clear_cache()
    con_posiciones = {
        row.symbol: (row.rank, row.score)
        for row in opportunity_service.compute_opportunities(
            db, otra, assets=assets, limit=50
        ).opportunities
    }

    assert vacia == con_posiciones, (
        "El score y el puesto tienen que ser idénticos desde cualquier cartera"
    )


def test_the_fit_with_the_portfolio_is_still_reported_but_adds_nothing(db, universe):
    """La diversificación no desaparece: deja de sumar.

    Sigue diciendo cuánto pesa ya ese cubo en lo que tienes, que es útil. Lo
    que no hace es mover el ranking, y el peso cero lo declara en la propia
    respuesta en vez de dejarlo como un detalle de implementación.
    """
    portfolio, assets = universe
    db.add(buy(portfolio, assets[0], dt.datetime.now(dt.UTC) - dt.timedelta(days=5), fx="1"))
    # Sin cotización no hay posición valorada, y sin posición valorada el
    # encaje no se puede medir para nadie.
    db.add(AssetQuote(
        asset_id=assets[0].id, price=150.0, currency="USD",
        fetched_at=dt.datetime.now(dt.UTC), is_stale=False, source="test",
    ))
    db.commit()

    opportunity_service.clear_cache()
    respuesta = opportunity_service.compute_opportunities(
        db, portfolio, assets=assets, limit=50
    )

    assert respuesta.weights["diversification"] == 0.0
    for fila in respuesta.opportunities:
        assert fila.diversification.weight == 0.0
        assert fila.diversification.contribution == 0.0
    # Y la medición sigue ahí: el cubo que se posee pesa distinto de cero.
    medidos = [f for f in respuesta.opportunities if f.diversification.available]
    assert medidos, "Con una posición abierta, el encaje SÍ se puede medir"
    assert any(f.diversification.score < 100.0 for f in medidos)


def test_the_score_still_spans_the_full_scale(db, universe):
    """Quitar un factor no puede dejar el máximo alcanzable en 85.

    La división por la suma de pesos es una constante positiva: no cambia
    ningún puesto, solo la escala en que se lee el número.
    """
    portfolio, assets = universe
    opportunity_service.clear_cache()
    respuesta = opportunity_service.compute_opportunities(
        db, portfolio, assets=assets, limit=50
    )
    pesos = respuesta.weights
    maximo = (pesos["value"] + pesos["momentum"]) * 100 + pesos["baseline"]
    assert maximo == pytest.approx(100.0, abs=0.05)
    assert pesos["baseline"] + pesos["risk"] * 100 == pytest.approx(0.0, abs=0.05)
