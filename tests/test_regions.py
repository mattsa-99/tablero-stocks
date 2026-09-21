"""Región de mercado y filtrado del ranking de oportunidades."""

from __future__ import annotations

import pytest

from app.models import Asset, AssetType
from app.services.grading import Grade, parse_quality_tiers
from app.services.regions import MarketRegion, classify, parse_regions


def asset(symbol: str, **kwargs) -> Asset:
    kwargs.setdefault("asset_type", AssetType.STOCK)
    return Asset(symbol=symbol, **kwargs)


# ----------------------------------------------------------------------
# La región es el domicilio, no la bolsa
# ----------------------------------------------------------------------


def test_an_adr_belongs_to_its_company_country_not_to_wall_street():
    """CIB cotiza en Nueva York y es Bancolombia.

    Es LA decisión de este módulo. El universo se financia en USD y COP a
    propósito, así que casi todo cotiza en bolsas estadounidenses: clasificar
    por bolsa devolvería ~95% en "EE.UU." y dejaría Europa y LatAm vacías,
    que es tanto como no tener filtro.
    """
    assert classify(asset("CIB", country="Colombia")) is MarketRegion.COL
    assert classify(asset("PBR", country="Brazil")) is MarketRegion.LATAM
    assert classify(asset("SAP", country="Germany")) is MarketRegion.EU
    assert classify(asset("NVDA", country="United States")) is MarketRegion.US


def test_colombia_is_its_own_region_not_lumped_into_latam():
    """Es el mercado local del usuario: mezclarla con Brasil pierde el matiz."""
    assert classify(asset("ETB.CL", country="Colombia")) is MarketRegion.COL
    assert classify(asset("EWZ", country=None, asset_type=AssetType.ETF)) is (
        MarketRegion.LATAM
    )


def test_country_etfs_are_declared_because_they_have_no_country():
    """Yahoo no da país para un ETF: su geografía hay que declararla."""
    for symbol, expected in (
        ("GXG", MarketRegion.COL),
        ("EWW", MarketRegion.LATAM),
        ("VGK", MarketRegion.EU),
        ("EWG", MarketRegion.EU),
    ):
        assert classify(asset(symbol, asset_type=AssetType.ETF)) is expected


def test_crypto_and_futures_belong_to_no_national_economy():
    assert classify(asset("BTC-USD", asset_type=AssetType.CRYPTO)) is MarketRegion.GLOBAL
    assert classify(asset("GC=F", asset_type=AssetType.OTHER)) is MarketRegion.GLOBAL


def test_commodity_and_world_etfs_are_not_the_us_market():
    """GLD cotiza en EE.UU. pero sigue al oro; VT sigue al mundo entero.

    La región de un fondo se resuelve por su CUBO, y el cubo por la categoría
    del proveedor: por eso los activos de este test la llevan.
    """
    def fondo(symbol, category):
        return asset(symbol, asset_type=AssetType.ETF, fund_category=category)

    assert classify(fondo("GLD", "Commodities Focused")) is MarketRegion.GLOBAL
    assert classify(fondo("VT", "Global Large-Stock Blend")) is MarketRegion.GLOBAL
    # Un ETF de bitcoin al contado tampoco es la economía estadounidense.
    assert classify(fondo("IBIT", "Digital Assets")) is MarketRegion.GLOBAL
    # Un sectorial o de bonos del Tesoro sí es mercado estadounidense.
    assert classify(fondo("XLE", "Equity Energy")) is MarketRegion.US
    assert classify(fondo("TLT", "Long Government")) is MarketRegion.US


def test_classification_never_returns_none():
    """GLOBAL es el cajón honesto: un activo sin región rompería el filtro."""
    assert classify(asset("DESCONOCIDO")) is MarketRegion.GLOBAL


def test_ticker_suffix_only_decides_when_the_provider_has_no_country():
    """El país manda sobre el sufijo: el sufijo solo dice dónde se negocia."""
    # Sin país todavía (activo recién creado), el sufijo salva la papeleta.
    assert classify(asset("NUTRESA.CL")) is MarketRegion.COL
    # Con país, gana el país aunque el sufijo sugiera otra cosa.
    assert classify(asset("XYZ.SA", country="United States")) is MarketRegion.US


# ----------------------------------------------------------------------
# Parseo de los parámetros
# ----------------------------------------------------------------------


def test_absent_filter_means_everything_not_nothing():
    """None = sin filtrar. Un set vacío dejaría la vista en blanco."""
    assert parse_regions(None) is None
    assert parse_regions("") is None
    assert parse_quality_tiers(None) is None


def test_unknown_values_are_ignored_rather_than_rejected():
    """Un 422 por un parámetro de interfaz dejaría la pantalla vacía."""
    assert parse_regions("MARTE") is None
    assert parse_regions("US,MARTE") == {MarketRegion.US}
    assert parse_quality_tiers("inventada") is None


def test_parsing_is_case_insensitive_and_multi_valued():
    assert parse_regions("us,col") == {MarketRegion.US, MarketRegion.COL}
    assert parse_quality_tiers("muy_buena, BUENA") == {Grade.EXCELLENT, Grade.GOOD}


def test_slugs_cover_every_grade():
    """Si una calificación no tuviera slug, sería infiltrable desde la URL."""
    from app.services.grading import GRADE_SLUG

    assert set(GRADE_SLUG) == set(Grade)


# ----------------------------------------------------------------------
# Filtrado del ranking
# ----------------------------------------------------------------------


@pytest.fixture
def opportunity_portfolio(db):
    """Universo variado: varias regiones y varias calidades a la vez.

    Se construye a mano en vez de usar el catálogo real para que el test no
    dependa de precios de mercado que cambian cada día.
    """
    import datetime as dt

    from app.models import FundamentalSnapshot, Portfolio, PriceHistory

    today = dt.date.today()
    # (símbolo, país, tendencia diaria, P/E) -> calidades y regiones distintas.
    spec = [
        ("NVDA", "United States", 1.004, 15.0),
        ("KO", "United States", 1.003, 18.0),
        ("MSFT", "United States", 0.998, 80.0),
        ("XOM", "United States", 1.002, 12.0),
        ("CIB", "Colombia", 1.005, 9.0),
        ("ETB.CL", "Colombia", 0.997, 70.0),
        ("PBR", "Brazil", 1.004, 8.0),
        ("AMX", "Mexico", 1.001, 20.0),
        ("SAP", "Germany", 1.002, 30.0),
        ("NSRGY", "Switzerland", 1.001, 22.0),
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

    # Ancla de valoración absoluta.
    spy = Asset(symbol="SPY", name="S&P 500", currency="USD", asset_type=AssetType.ETF)
    db.add(spy)
    db.flush()
    db.add(FundamentalSnapshot(asset_id=spy.id, as_of=today, trailing_pe=25.0,
                               fetched_at=dt.datetime.now(dt.UTC)))
    created.append(spy)

    portfolio = Portfolio(name="Vacío", base_currency="USD")
    db.add(portfolio)
    db.commit()
    return portfolio, created


@pytest.fixture
def scored(db, opportunity_portfolio):
    """Ranking completo, sin filtros, para comparar contra el filtrado."""
    from app.services.opportunities import compute_opportunities

    portfolio, assets = opportunity_portfolio
    return compute_opportunities(db, portfolio, assets=assets, limit=50)


def test_filtering_does_not_change_any_score(db, opportunity_portfolio, scored):
    """LA invariante. El score es un percentil: depende del universo.

    Si el filtro se aplicara ANTES de puntuar, pedir "solo Colombia"
    recalcularía los percentiles entre un puñado de activos y un valor
    mediocre saldría con 90 puntos por no tener rivales. Se filtra DESPUÉS,
    así que el score de cada fila es idéntico con y sin filtro.
    """
    from app.services.opportunities import compute_opportunities

    portfolio, assets = opportunity_portfolio
    filtered = compute_opportunities(
        db, portfolio, assets=assets, limit=50, quality_tiers={Grade.EXCELLENT}
    )

    unfiltered_scores = {row.symbol: row.score for row in scored.opportunities}
    for row in filtered.opportunities:
        assert row.score == unfiltered_scores[row.symbol]
    assert filtered.universe_size == scored.universe_size


def test_rank_is_the_position_in_the_full_ranking(db, opportunity_portfolio, scored):
    """Ver que el mejor colombiano es el #7 de 494 es información.

    Renumerar la lista filtrada a #1 la destruiría, y además insinuaría que
    ese activo encabeza el ranking cuando solo encabeza el subconjunto.
    """
    from app.services.opportunities import compute_opportunities

    portfolio, assets = opportunity_portfolio
    unfiltered_rank = {row.symbol: row.rank for row in scored.opportunities}

    filtered = compute_opportunities(
        db, portfolio, assets=assets, limit=50, quality_tiers={Grade.EXCELLENT}
    )
    for row in filtered.opportunities:
        assert row.rank == unfiltered_rank[row.symbol]


def test_counts_describe_the_whole_universe_not_the_filtered_result(
    db, opportunity_portfolio, scored
):
    """Si los recuentos siguieran al filtro, los demás chips marcarían 0.

    El usuario se quedaría sin saber qué le queda por explorar y sin forma
    obvia de deshacer el filtro.
    """
    from app.services.opportunities import compute_opportunities

    portfolio, assets = opportunity_portfolio
    filtered = compute_opportunities(
        db, portfolio, assets=assets, limit=50, regions={MarketRegion.COL}
    )
    assert filtered.grade_counts == scored.grade_counts
    assert filtered.region_counts == scored.region_counts


def test_matched_size_reports_what_the_filter_leaves_visible(
    db, opportunity_portfolio, scored
):
    from app.services.opportunities import compute_opportunities

    portfolio, assets = opportunity_portfolio
    assert scored.matched_size == scored.universe_size  # sin filtros

    filtered = compute_opportunities(
        db, portfolio, assets=assets, limit=50, regions={MarketRegion.US}
    )
    assert filtered.matched_size <= scored.universe_size
    assert filtered.matched_size == len(filtered.opportunities)
    assert all(r.market_region == "US" for r in filtered.opportunities)


def test_filters_combine_as_an_intersection(db, opportunity_portfolio):
    from app.services.opportunities import compute_opportunities

    portfolio, assets = opportunity_portfolio
    combined = compute_opportunities(
        db,
        portfolio,
        assets=assets,
        limit=50,
        regions={MarketRegion.US},
        quality_tiers={Grade.EXCELLENT, Grade.GOOD},
    )
    for row in combined.opportunities:
        assert row.market_region == "US"
        assert row.assessment.grade in {"A", "B"}


def test_limit_applies_after_filtering_not_before(db, opportunity_portfolio):
    """Recortar antes de filtrar dejaría casi siempre una lista vacía.

    Con `limit=10` sobre 491 candidatos, filtrar dentro de esos 10 devolvería
    dos o tres filas y parecería que no hay nada de esa región.
    """
    from app.services.opportunities import compute_opportunities

    portfolio, assets = opportunity_portfolio
    narrow = compute_opportunities(
        db, portfolio, assets=assets, limit=3, regions={MarketRegion.US}
    )
    assert len(narrow.opportunities) == min(3, narrow.matched_size)
    assert all(r.market_region == "US" for r in narrow.opportunities)


def test_asian_companies_have_their_own_region():
    """Toyota y Alibaba no son "ETFs globales ni cripto"."""
    assert classify(asset("TM", country="Japan")) is MarketRegion.ASIA
    assert classify(asset("BABA", country="China")) is MarketRegion.ASIA
    assert classify(asset("TSM", country="Taiwan")) is MarketRegion.ASIA
    assert classify(asset("INFY", country="India")) is MarketRegion.ASIA
    assert classify(asset("EWJ", asset_type=AssetType.ETF)) is MarketRegion.ASIA
    assert classify(asset("MCHI", asset_type=AssetType.ETF)) is MarketRegion.ASIA


def test_asia_pacific_is_not_asia():
    """Australia y Canadá quedan FUERA de Asia deliberadamente.

    Los índices suelen decir "Asia-Pacífico" e incluir Australia, pero el chip
    de la interfaz dice "Asia" a secas: meter allí a BHP haría que el filtro
    afirmara algo falso. Sin cubo propio, GLOBAL es el sitio honesto.
    """
    assert classify(asset("BHP", country="Australia")) is MarketRegion.GLOBAL
    assert classify(asset("RY", country="Canada")) is MarketRegion.GLOBAL
    assert classify(asset("GOLD", country="South Africa")) is MarketRegion.GLOBAL


def test_every_region_has_a_label_and_a_place_in_the_order():
    """Una región sin etiqueta saldría como código crudo en la interfaz."""
    from app.services.regions import REGION_LABEL, REGION_ORDER

    assert set(REGION_LABEL) == set(MarketRegion)
    assert set(REGION_ORDER) == set(MarketRegion)


# ----------------------------------------------------------------------
# El frontend duplica estas tablas: no pueden divergir
# ----------------------------------------------------------------------


def _js_constants() -> str:
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    return (root / "app" / "static" / "js" / "opportunities.js").read_text(
        encoding="utf-8"
    )


def test_the_frontend_knows_every_region():
    """Añadir una región solo en Python la dejaría sin chip, en silencio.

    El JS mantiene su propia copia de la tabla -no hay build que la genere-,
    así que la única defensa contra la divergencia es comprobarlo aquí. Pasó
    justo al añadir Asia: hubo que tocar cuatro sitios.
    """
    import re

    source = _js_constants()
    match = re.search(r"const REGION_ORDER = \[(.*?)\];", source, re.S)
    assert match, "No se encontró REGION_ORDER en opportunities.js"
    in_js = set(re.findall(r'"([A-Z]+)"', match.group(1)))

    assert in_js == {r.value for r in MarketRegion}, (
        "REGION_ORDER del JS no coincide con MarketRegion. "
        f"Solo en JS: {in_js - {r.value for r in MarketRegion}}; "
        f"solo en Python: {{r.value for r in MarketRegion}} - in_js"
    )
    for region in MarketRegion:
        assert f'{region.value}:' in source or f'"{region.value}"' in source


def test_the_frontend_uses_the_same_quality_slugs():
    """Un slug distinto haría que el filtro de calidad no filtrara nada."""
    import re

    from app.services.grading import GRADE_SLUG

    source = _js_constants()
    match = re.search(r"const GRADE_SLUG = \{(.*?)\};", source, re.S)
    assert match, "No se encontró GRADE_SLUG en opportunities.js"
    in_js = dict(re.findall(r"(\w+):\s*\"(\w+)\"", match.group(1)))

    expected = {grade.value: slug for grade, slug in GRADE_SLUG.items()}
    assert in_js == expected, f"JS={in_js} vs Python={expected}"

