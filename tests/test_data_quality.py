"""Datos rotos del proveedor y series de precios detenidas.

Los dos fallos que estas pruebas fijan comparten lo peor: producen un número
plausible sin lanzar nada, y ese número decide qué se le recomienda comprar
al usuario.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.services.asset_class import AssetClass
from app.services.data_quality import (
    EARNINGS_INPUTS,
    PLAUSIBLE_RANGES,
    PRICE_TO_BOOK_RATIOS,
    VALUATION_MULTIPLES,
    fresh_trailing_pe,
    sanitise_multiples,
    sanitise_price_ratios,
)


def ratios(**kwargs) -> dict[str, float | None]:
    base = dict.fromkeys(PRICE_TO_BOOK_RATIOS)
    base.update(kwargs)
    return base


def multiples(**kwargs) -> dict[str, float | None]:
    base = dict.fromkeys((*VALUATION_MULTIPLES, *EARNINGS_INPUTS))
    base.update(kwargs)
    return base


# ----------------------------------------------------------------------
# Tercera red: esto no es una empresa
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("symbol", "clase", "entrada"),
    [
        # Yahoo le asigna un P/E de 0,89 a un fondo de bonos basura. Era el
        # P/E positivo más bajo de los 494 del universo, así que SJNK salía con
        # la MEJOR valoración de todas: 99,9 sobre 100.
        ("SJNK", AssetClass.RENTA_FIJA, {"trailing_pe": 0.89050037}),
        # TLT: `bookValue` 148,87 contra un precio de 80,93 da un P/B de 0,54 y
        # una valoración de 97,5. El propio Yahoo publica `navPrice` = 80,92 en
        # la MISMA respuesta: sus dos cifras se contradicen.
        ("TLT", AssetClass.RENTA_FIJA, {"price_to_book": 0.54216903}),
        # Oro físico. No hay ni beneficios ni libros que valgan.
        ("GLD", AssetClass.MATERIAS_PRIMAS, {"price_to_book": 2.3595877}),
        ("GSG", AssetClass.MATERIAS_PRIMAS, {"trailing_pe": 8.05, "price_to_book": 1.57}),
        # PHYS solo tiene oro, y su «beneficio por acción» de 5,51 es la
        # revalorización del metal: cuanto más sube el oro, más barato parece.
        ("PHYS", AssetClass.DESCONOCIDO, {"trailing_pe": 5.885662}),
        ("BTC-USD", AssetClass.CRIPTO, {"trailing_pe": 12.0}),
        ("ES=F", AssetClass.DERIVADO, {"trailing_pe": 20.0}),
    ],
)
def test_an_asset_with_no_companies_inside_gets_no_multiple(symbol, clase, entrada):
    """El número es plausible y aun así no mide nada. Ese es el fallo.

    Las dos redes anteriores buscan cifras IMPLAUSIBLES: 0,89 y 0,54 pasan las
    dos sin despeinarse. Lo que falla no es la magnitud, es que detrás no hay
    beneficios ni patrimonio contable.
    """
    resultado = sanitise_multiples(
        multiples(**entrada),
        quote_currency="USD",
        financial_currency="USD",
        asset_class=clase,
    )
    assert all(resultado.values[k] is None for k in VALUATION_MULTIPLES), symbol
    assert resultado.has_drops


def test_the_earnings_per_share_falls_with_the_multiples():
    """Anular el P/E sin anular el BPA no sirve de nada.

    El motor rehace el P/E con el precio de hoy a partir de `eps_trailing`
    (`fresh_trailing_pe`), así que un BPA superviviente reconstruye el múltiplo
    en cada petición y pisa el None.

    Lo descubrió PHYS: con sus múltiplos ya descartados seguía saliendo con
    una valoración de 99,1 sobre 100. Su «beneficio por acción» de 5,51 es la
    revalorización del oro.
    """
    resultado = sanitise_multiples(
        multiples(trailing_pe=5.885662, eps_trailing=5.51),
        quote_currency="USD",
        financial_currency="USD",
        asset_class=AssetClass.DESCONOCIDO,
    )
    assert resultado.values["trailing_pe"] is None
    assert resultado.values["eps_trailing"] is None
    assert fresh_trailing_pe(32.36, resultado.values["eps_trailing"]) is None


def test_a_real_company_keeps_its_earnings_per_share():
    resultado = sanitise_multiples(
        multiples(trailing_pe=9.6, eps_trailing=10.06),
        quote_currency="USD",
        financial_currency="USD",
        asset_class=AssetClass.ACCION,
    )
    assert resultado.values["eps_trailing"] == 10.06


def test_a_fund_of_shares_keeps_its_pe_but_loses_its_book_value():
    """Son dos preguntas distintas y solo una tiene respuesta.

    El P/E de un fondo de acciones es la media ponderada del de su cartera, y
    es real: Yahoo da 24,40 para IVV, que es donde cotiza el S&P 500. El valor
    en libros no: el de un fondo ES su NAV, y el precio lo sigue por arbitraje,
    así que el cociente vale ~1 siempre. Cuando sale distinto está roto y
    cuando sale bien no informa.
    """
    resultado = sanitise_multiples(
        multiples(trailing_pe=24.401787, price_to_book=1.7612206),
        quote_currency="USD",
        financial_currency="USD",
        asset_class=AssetClass.FONDO_ACCIONES,
    )
    assert resultado.values["trailing_pe"] == 24.401787
    assert resultado.values["price_to_book"] is None


def test_a_gold_miner_fund_is_equity_and_keeps_its_pe():
    """GDX tiene mineras dentro, no oro. Su P/E es el de unas empresas.

    Es la distinción que una clasificación por palabras sueltas se comería:
    «Equity Precious Metals» suena a metales y es renta variable.
    """
    resultado = sanitise_multiples(
        multiples(trailing_pe=12.254289),
        quote_currency="USD",
        financial_currency="USD",
        asset_class=AssetClass.FONDO_ACCIONES,
    )
    assert resultado.values["trailing_pe"] == 12.254289


def test_a_real_company_still_goes_through_the_two_original_nets():
    """La red nueva no puede desactivar a las viejas."""
    resultado = sanitise_multiples(
        multiples(trailing_pe=9.6, price_to_book=0.0022),
        quote_currency="USD",
        financial_currency="COP",
        asset_class=AssetClass.ACCION,
    )
    assert resultado.values["trailing_pe"] == 9.6, "El P/E de una empresa no se toca"
    assert resultado.values["price_to_book"] is None, "CIB: divisas distintas"


# ----------------------------------------------------------------------
# Ratios contaminados por la divisa
# ----------------------------------------------------------------------


def test_a_currency_mismatch_discards_the_price_to_accounting_ratios():
    """CIB cotiza en USD y reporta en COP: su P/B viene 800 veces bajo.

    Medido: con el 0,0022 su valoración sale 94,6 y encabeza el ranking; con
    un valor plausible baja a 84,7 y cae del puesto 1 al 4. Un dato roto
    decidía la primera recomendación.
    """
    result = sanitise_price_ratios(
        ratios(price_to_book=0.0021651, price_to_sales=1.2, ev_to_ebitda=8.0),
        quote_currency="USD",
        financial_currency="COP",
    )
    assert result.values["price_to_book"] is None
    assert result.values["price_to_sales"] is None
    assert result.values["ev_to_ebitda"] is None
    assert "COP" in result.dropped["price_to_book"]


def test_the_same_failure_in_the_opposite_direction():
    """MINEROS.CL cotiza en COP y reporta en USD: su P/B viene 9.267.

    El error va en las dos direcciones. Inflado, hace que el activo parezca
    el más caro del universo y lo penaliza; no es más benigno que el otro.
    """
    result = sanitise_price_ratios(
        ratios(price_to_book=9267.6, price_to_sales=5528.2),
        quote_currency="COP",
        financial_currency="USD",
    )
    assert result.values["price_to_book"] is None
    assert result.values["price_to_sales"] is None


def test_matching_currencies_keep_even_an_extreme_ratio():
    """Colgate tiene un P/B real de ~303, por recompras que vacían el balance.

    Es la razón de no usar SOLO una banda de magnitud: cortar por lo alto
    descartaría un dato correcto y raro.
    """
    result = sanitise_price_ratios(
        ratios(price_to_book=302.8), quote_currency="USD", financial_currency="USD"
    )
    assert result.values["price_to_book"] == 302.8
    assert not result.has_drops


def test_magnitude_catches_what_the_currency_check_cannot():
    """BRK-B cotiza y reporta en USD, y aun así da 0,001.

    Ahí no hay mezcla de divisas: se compara el precio de la clase B con el
    valor contable de la clase A. Sin la segunda red pasaría entero.
    """
    result = sanitise_price_ratios(
        ratios(price_to_book=0.00097), quote_currency="USD", financial_currency="USD"
    )
    assert result.values["price_to_book"] is None
    assert "plausible" in result.dropped["price_to_book"]


def test_an_unknown_financial_currency_does_not_discard_anything():
    """Sin el dato no se puede afirmar que haya desajuste: se deja pasar.

    Descartar por si acaso dejaría al motor sin el sub-factor para medio
    universo cada vez que el proveedor se dejara un campo.
    """
    result = sanitise_price_ratios(
        ratios(price_to_book=2.5), quote_currency="USD", financial_currency=None
    )
    assert result.values["price_to_book"] == 2.5


def test_the_pe_is_never_discarded_because_it_is_not_affected():
    """Yahoo SÍ convierte el beneficio antes de dividir.

    Comprobado contra el proveedor en CIB, MINEROS.CL, TM, ASML y AAPL:
    `precio / eps` coincide exactamente con su `trailingPE`. Por eso el P/E
    sobrevive al filtro y además se puede recalcular.
    """
    assert "trailing_pe" not in PRICE_TO_BOOK_RATIOS
    assert "forward_pe" not in PRICE_TO_BOOK_RATIOS


def test_absent_ratios_are_left_alone():
    result = sanitise_price_ratios(
        ratios(), quote_currency="USD", financial_currency="COP"
    )
    assert not result.has_drops
    assert all(v is None for v in result.values.values())


@pytest.mark.parametrize("ratio", PRICE_TO_BOOK_RATIOS)
def test_every_guarded_ratio_has_a_plausible_range(ratio):
    """Sin banda, la segunda red no existe para ese ratio."""
    low, high = PLAUSIBLE_RANGES[ratio]
    assert 0 < low < high


# ----------------------------------------------------------------------
# P/E con el precio de ahora
# ----------------------------------------------------------------------


def test_the_pe_follows_todays_price():
    """Es precio entre beneficio: se mueve a diario aunque el beneficio no."""
    assert fresh_trailing_pe(325.13, 8.71) == pytest.approx(37.33, abs=0.01)


def test_a_negative_result_is_not_a_bargain():
    """Un P/E negativo significa que la empresa pierde dinero, no que esté barata."""
    assert fresh_trailing_pe(100.0, -2.0) is None
    assert fresh_trailing_pe(100.0, 0.0) is None


def test_missing_data_yields_no_pe_rather_than_a_guess():
    assert fresh_trailing_pe(None, 5.0) is None
    assert fresh_trailing_pe(100.0, None) is None


# ----------------------------------------------------------------------
# Series de precios detenidas
# ----------------------------------------------------------------------


@pytest.fixture
def universe_with_a_frozen_series(db):
    """Dos activos idénticos salvo en que uno dejó de recibir precios."""
    from app.models import Asset, AssetQuote, AssetType, FundamentalSnapshot, PriceHistory

    today = dt.date.today()
    created = []
    for symbol, offset in (("VIVO", 0), ("PARADO", 45)):
        asset = Asset(
            symbol=symbol, name=symbol, currency="USD",
            asset_type=AssetType.STOCK, country="United States", sector="Energy",
        )
        db.add(asset)
        db.flush()
        price = 100.0
        # La MISMA serie en los dos; solo cambia dónde termina.
        for day in range(300):
            price *= 1.003
            db.add(PriceHistory(
                asset_id=asset.id,
                date=today - dt.timedelta(days=300 - day + offset),
                close=price, adj_close=price,
            ))
        db.add(AssetQuote(asset_id=asset.id, price=price, currency="USD",
                          fetched_at=dt.datetime.now(dt.UTC)))
        db.add(FundamentalSnapshot(
            asset_id=asset.id, as_of=today, trailing_pe=15.0, eps_trailing=5.0,
            return_on_equity=0.18, profit_margin=0.12, debt_to_equity=60.0,
            revenue_growth=0.1, fetched_at=dt.datetime.now(dt.UTC),
        ))
        created.append(asset)

    # Relleno para superar el universo mínimo del motor.
    for i in range(6):
        filler = Asset(symbol=f"F{i}", name=f"F{i}", currency="USD",
                       asset_type=AssetType.STOCK, country="United States",
                       sector="Technology")
        db.add(filler)
        db.flush()
        price = 50.0
        for day in range(300):
            price *= 1.001
            db.add(PriceHistory(asset_id=filler.id,
                                date=today - dt.timedelta(days=300 - day),
                                close=price, adj_close=price))
        db.add(AssetQuote(asset_id=filler.id, price=price, currency="USD",
                          fetched_at=dt.datetime.now(dt.UTC)))
        db.add(FundamentalSnapshot(asset_id=filler.id, as_of=today,
                                   trailing_pe=20.0 + i, eps_trailing=4.0,
                                   fetched_at=dt.datetime.now(dt.UTC)))
        created.append(filler)

    from app.models import Portfolio

    portfolio = Portfolio(name="Vacío", base_currency="USD")
    db.add(portfolio)
    db.commit()
    return portfolio, created


def test_a_frozen_series_is_not_scored_on_trend_or_risk(
    db, universe_with_a_frozen_series
):
    """El fallo que esto corrige no se veía en pantalla.

    Una serie parada calcula sus indicadores igual de bien que una viva, así
    que el activo salía con `data_completeness` 1.00 y confianza «alta». GXG
    llegó al puesto 8 con la última barra de hacía seis semanas.
    """
    from app.services.opportunities import compute_opportunities

    portfolio, assets = universe_with_a_frozen_series
    result = compute_opportunities(db, portfolio, assets=assets, limit=50)

    by_symbol = {o.symbol: o for o in result.opportunities}

    # El vivo se puntúa con normalidad.
    assert by_symbol["VIVO"].momentum.available

    # El parado, o se excluye o aparece SIN esos factores y con la nota.
    if "PARADO" in by_symbol:
        frozen = by_symbol["PARADO"]
        assert not frozen.momentum.available
        assert not frozen.risk.available
        assert any("detenido" in note for note in frozen.notes)
    else:
        assert "PARADO" in result.excluded


def test_the_freshness_threshold_tolerates_a_long_weekend(db):
    """Tres o cuatro días sin barra son un puente, no un histórico abandonado."""
    from app.core.config import settings

    assert settings.price_series_max_age_days >= 7, (
        "Un umbral corto marcaría como parado cualquier valor poco líquido "
        "de la BVC después de un festivo"
    )


# ----------------------------------------------------------------------
# Fundamentales viejos: puntúan igual de bien, y eso es el problema
# ----------------------------------------------------------------------


def _universo_con_fundamentales(db, edades_en_dias: list[int]):
    """Un universo donde cada activo tiene fundamentales de una edad dada."""
    from app.models import Asset, AssetQuote, FundamentalSnapshot, Portfolio, PriceHistory

    hoy = dt.date.today()
    creados = []
    for i, edad in enumerate(edades_en_dias):
        item = Asset(
            symbol=f"SYM{i}", currency="USD", sector="Technology", is_universe=True
        )
        db.add(item)
        db.flush()
        precio = 100.0
        for dia in range(300):
            precio *= 1.002
            db.add(PriceHistory(
                asset_id=item.id, date=hoy - dt.timedelta(days=300 - dia),
                close=precio, adj_close=precio,
            ))
        db.add(FundamentalSnapshot(
            asset_id=item.id, as_of=hoy - dt.timedelta(days=edad),
            trailing_pe=12.0 + i, return_on_equity=0.2, profit_margin=0.15,
            debt_to_equity=30.0, revenue_growth=0.1,
            fetched_at=dt.datetime.now(dt.UTC),
        ))
        db.add(AssetQuote(
            asset_id=item.id, price=precio, currency="USD",
            fetched_at=dt.datetime.now(dt.UTC), is_stale=False, source="test",
        ))
        creados.append(item)
    portfolio = Portfolio(name="Frescura", base_currency="USD")
    db.add(portfolio)
    db.commit()
    return portfolio, creados


def test_stale_fundamentals_lower_the_confidence_of_the_row(db):
    """Es el mismo fallo silencioso que ya mordió con las series de precios.

    GXG llegó al puesto 8 con `data_completeness` 1.00 y «confianza alta»
    sobre una barra de hacía seis semanas: el indicador mide si el factor se
    pudo CALCULAR, no si el dato está fresco. Con los fundamentales pasaba
    igual, y la sincronización con Yahoo sale PARCIAL con frecuencia.
    """
    from app.services import opportunities as opp

    portfolio, assets = _universo_con_fundamentales(db, [0, 0, 0, 0, 30, 30])
    respuesta = opp.compute_opportunities(db, portfolio, assets=assets, limit=50)
    por_simbolo = {r.symbol: r for r in respuesta.opportunities}

    fresco = por_simbolo["SYM0"]
    rancio = por_simbolo["SYM4"]

    assert fresco.data_completeness == rancio.data_completeness, (
        "Los dos se pudieron calcular igual de bien: ese es el punto"
    )
    assert fresco.fundamentals_age_days == 0
    assert rancio.fundamentals_age_days == 30
    assert fresco.confidence == "high"
    assert rancio.confidence == "medium", "Lo viejo no invalida, pero rebaja"
    assert any("días" in n for n in rancio.notes)


def test_the_response_says_how_much_of_the_universe_is_stale(db):
    """`fundamentals_oldest` no distingue UN activo viejo de media tabla vieja.

    Medido el 21-09-2026 tras varias corridas PARCIALES: 275 de 497 activos
    tenían fundamentales del día y 222 de entre dos y seis días. Con una sola
    fecha, el peor caso y el caso bueno se ven exactamente igual.
    """
    from app.services import opportunities as opp

    portfolio, assets = _universo_con_fundamentales(db, [0, 0, 0, 30, 30, 30])
    respuesta = opp.compute_opportunities(db, portfolio, assets=assets, limit=50)

    assert respuesta.freshness.fundamentals_stale_count == 3
    assert respuesta.freshness.fundamentals_fresh_pct == 50.0
    assert any("3 de 6 candidatos" in w for w in respuesta.warnings)


def test_a_healthy_day_raises_no_warning(db):
    """El aviso tiene que distinguir un día malo de uno normal."""
    from app.services import opportunities as opp

    portfolio, assets = _universo_con_fundamentales(db, [0, 1, 2, 2, 3, 4])
    respuesta = opp.compute_opportunities(db, portfolio, assets=assets, limit=50)

    assert respuesta.freshness.fundamentals_stale_count == 0
    assert respuesta.freshness.fundamentals_fresh_pct == 100.0
    assert not any("fundamentales de más de" in w for w in respuesta.warnings)
