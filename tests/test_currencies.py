"""Divisas internacionales: subunidades, bandas de sanidad y triangulación.

Los tres fallos que estas pruebas fijan comparten una propiedad peligrosa: no
levantan ninguna excepción. Producen un número plausible y equivocado que se
propaga hasta el valor de la cartera, y en el caso de una compra queda además
congelado para siempre en `transactions.fx_rate_to_base`.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.providers.base import QuoteData
from app.providers.currencies import (
    may_quote_in_minor_units,
    normalize_currency,
    scale,
)
from app.services.market_data import MarketDataService, fx_sanity_check

# ----------------------------------------------------------------------
# Subunidades
# ----------------------------------------------------------------------


def test_gbp_pence_is_not_gbp():
    """"GBp" y "GBP" difieren SOLO en la caja, y valen 100 veces distinto."""
    assert normalize_currency("GBp") == ("GBP", 100.0)
    assert normalize_currency("GBP") == ("GBP", 1.0)


def test_uppercasing_alone_would_lose_the_distinction():
    """El fallo original: `.upper()` convierte peniques en libras en silencio."""
    assert "GBp".upper() == "GBP"  # la razón por la que hay que normalizar antes
    iso, divisor = normalize_currency("GBp")
    assert iso == "GBP" and divisor == 100.0
    # Shell cotizaba a 3344.5 peniques: son 33,45 GBP, no 3.344,50.
    assert scale(3344.5, divisor) == pytest.approx(33.445)


def test_other_minor_unit_currencies():
    assert normalize_currency("ILA") == ("ILS", 100.0)  # agorot
    assert normalize_currency("ZAc") == ("ZAR", 100.0)  # centavos


def test_absent_currency_stays_absent():
    """La ausencia NO se rellena con USD: valoraría el activo con otro tipo."""
    assert normalize_currency(None) == (None, 1.0)
    assert normalize_currency("  ") == (None, 1.0)


def test_scale_preserves_absence():
    """None no es 0: un 0 sería una pérdida del 100% inexistente."""
    assert scale(None, 100.0) is None


def test_only_minor_unit_exchanges_are_probed():
    """El sondeo de divisa cuesta una llamada: solo donde puede haber subunidad."""
    assert may_quote_in_minor_units("SHEL.L")
    assert may_quote_in_minor_units("TEVA.TA")
    assert not may_quote_in_minor_units("AAPL")
    assert not may_quote_in_minor_units("SAP.DE")


# ----------------------------------------------------------------------
# Bandas de sanidad derivadas
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "base,quote,good,inverted",
    [
        ("USD", "COP", 3_219.0, 0.00031),
        ("JPY", "COP", 20.13, 0.0497),
        ("HKD", "USD", 0.1276, 7.84),
        ("MXN", "COP", 189.37, 0.00528),
        ("KRW", "COP", 2.35, 0.4255),
    ],
)
def test_inversion_is_rejected_for_pairs_without_an_explicit_range(
    base, quote, good, inverted
):
    """La banda se deriva de la magnitud: no hace falta una fila por par."""
    assert fx_sanity_check(base, quote, good) is None
    assert fx_sanity_check(base, quote, inverted) is not None


def test_near_parity_pairs_are_not_checked_by_magnitude():
    """Con r~1 la inversión cae dentro de la banda: se prefiere no comprobar.

    Un falso positivo rechazaría un tipo correcto y dejaría la cartera sin
    valorar, que es peor que no comprobar un par donde el error es acotado.
    """
    assert fx_sanity_check("EUR", "USD", 1.16) is None
    assert fx_sanity_check("EUR", "USD", 0.86) is None  # invertido, indetectable


def test_unknown_currency_is_not_checked_rather_than_rejected():
    assert fx_sanity_check("XYZ", "COP", 42.0) is None


def test_non_positive_rate_always_fails():
    assert fx_sanity_check("USD", "COP", 0.0) is not None
    assert fx_sanity_check("USD", "COP", -1.0) is not None


# ----------------------------------------------------------------------
# Triangulación
# ----------------------------------------------------------------------


class _PivotOnlyProvider:
    """Proveedor sin pares directos contra COP, como Yahoo de verdad.

    HKDCOP=X, MXNCOP=X y KRWCOP=X devuelven 404; los tramos contra USD, no.
    """

    def __init__(self) -> None:
        self.requested: list[tuple[str, str]] = []

    def fetch_fx_rates(self, pairs):
        self.requested.extend(pairs)
        table = {("HKD", "USD"): 0.1276, ("USD", "COP"): 3_219.18}
        return {p: table[p] for p in pairs if p in table}


def test_pair_without_a_direct_quote_is_triangulated_through_usd(db):
    """HKD/COP no existe en Yahoo, pero HKD/USD x USD/COP sí."""
    provider = _PivotOnlyProvider()
    report = MarketDataService(db, provider).refresh_fx([("HKD", "COP")])

    from app.models import FxRateDaily

    stored = db.get(FxRateDaily, ("HKD", "COP", dt.date.today()))
    assert stored is not None
    assert float(stored.rate) == pytest.approx(0.1276 * 3_219.18)
    assert report.fx_updated >= 1


def test_a_triangulated_rate_records_its_provenance(db):
    """Componer dos tipos no es lo mismo que medir uno: debe poder distinguirse."""
    MarketDataService(db, _PivotOnlyProvider()).refresh_fx([("HKD", "COP")])

    from app.models import FxRateDaily

    assert db.get(FxRateDaily, ("HKD", "COP", dt.date.today())).source == "yfinance:USD"
    # El tramo medido directamente NO se marca como triangulado.
    assert db.get(FxRateDaily, ("USD", "COP", dt.date.today())).source == "yfinance"


def test_triangulation_reuses_legs_already_fetched_today(db):
    """Los tramos compartidos se piden una vez, no una por par derivado."""
    provider = _PivotOnlyProvider()
    MarketDataService(db, provider).refresh_fx([("USD", "COP"), ("HKD", "COP")])
    assert provider.requested.count(("USD", "COP")) == 1


class _NothingProvider:
    def fetch_fx_rates(self, pairs):
        return {}


def test_a_pair_that_cannot_be_triangulated_is_rejected_not_assumed(db):
    """Sin tipo de cambio la operación se rechaza: asumir 1 erraría por ~4000x."""
    report = MarketDataService(db, _NothingProvider()).refresh_fx([("HKD", "COP")])

    from app.models import FxRateDaily

    assert db.get(FxRateDaily, ("HKD", "COP", dt.date.today())) is None
    assert report.fx_updated == 0
    assert any("HKD/COP" in w for w in report.warnings)


class _InvertedLegProvider:
    """Devuelve un tramo invertido: la composición debe seguir siendo revisada."""

    def fetch_fx_rates(self, pairs):
        table = {("HKD", "USD"): 0.1276, ("USD", "COP"): 1 / 3_219.18}
        return {p: table[p] for p in pairs if p in table}


def test_a_triangulated_rate_is_sanity_checked_like_a_direct_one(db):
    """Componer dos tramos también puede dar un disparate si uno viene al revés."""
    report = MarketDataService(db, _InvertedLegProvider()).refresh_fx([("HKD", "COP")])

    from app.models import FxRateDaily

    assert db.get(FxRateDaily, ("HKD", "COP", dt.date.today())) is None
    assert any("invertido" in w or "plausible" in w for w in report.warnings)


# ----------------------------------------------------------------------
# Catálogo
# ----------------------------------------------------------------------


def test_catalog_does_not_force_every_asset_to_usd(db, monkeypatch):
    """Un valor alemán sembrado como USD se valoraría con el tipo equivocado."""
    from app.models import Asset
    from app.services import catalog as catalog_service

    monkeypatch.setattr(
        catalog_service,
        "load_catalog",
        lambda: [
            {"symbol": "SAP.DE", "name": "SAP SE", "asset_type": "STOCK",
             "currency": "EUR", "exchange": "GER"},
            {"symbol": "AAPL", "name": "Apple Inc.", "asset_type": "STOCK"},
        ],
    )
    catalog_service.seed_catalog(db)

    assert db.query(Asset).filter_by(symbol="SAP.DE").one().currency == "EUR"
    # Sin divisa declarada se mantiene el USD por defecto.
    assert db.query(Asset).filter_by(symbol="AAPL").one().currency == "USD"


def test_quote_normalization_reaches_the_dataclass():
    """QuoteData nunca debe transportar peniques ni el código "GBp"."""
    iso, divisor = normalize_currency("GBp")
    quote = QuoteData(
        symbol="SHEL.L",
        price=scale(3344.5, divisor),
        previous_close=scale(3338.0, divisor),
        currency=iso,
        quote_time=None,
    )
    assert quote.currency == "GBP"
    assert quote.price == pytest.approx(33.445)
    assert quote.previous_close == pytest.approx(33.38)
