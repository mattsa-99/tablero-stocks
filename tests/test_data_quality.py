"""Datos rotos del proveedor y series de precios detenidas.

Los dos fallos que estas pruebas fijan comparten lo peor: producen un número
plausible sin lanzar nada, y ese número decide qué se le recomienda comprar
al usuario.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.services.data_quality import (
    PLAUSIBLE_RANGES,
    PRICE_TO_BOOK_RATIOS,
    fresh_trailing_pe,
    sanitise_price_ratios,
)


def ratios(**kwargs) -> dict[str, float | None]:
    base = dict.fromkeys(PRICE_TO_BOOK_RATIOS)
    base.update(kwargs)
    return base


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
