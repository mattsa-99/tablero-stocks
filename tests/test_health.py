"""Salud financiera y calendario leídos del payload crudo de Yahoo.

Los payloads de estos tests son reducciones de casos REALES de la base (AAPL,
PBR, TM, JPM, CIB, O): cada uno fija un fallo que ya se vio en producción, no
uno imaginado.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.services.health import extract_health

TODAY = dt.date(2026, 9, 20)


def ts(date: dt.date) -> float:
    return dt.datetime(date.year, date.month, date.day, 12, tzinfo=dt.UTC).timestamp()


AAPL = {
    "financialCurrency": "USD", "marketCap": 4.9e12, "sharesOutstanding": 14.59e9,
    "freeCashflow": 107.7e9, "operatingCashflow": 146.7e9, "totalCash": 62.4e9,
    "totalDebt": 84.3e9, "ebitda": 168.0e9, "totalRevenue": 466.8e9,
    "operatingMargins": 0.326, "currentRatio": 1.003, "payoutRatio": 0.12,
}


def health(raw, **kwargs):
    kwargs.setdefault("quote_currency", "USD")
    kwargs.setdefault("sector", "Technology")
    kwargs.setdefault("industry", "Consumer Electronics")
    kwargs.setdefault("price", 336.0)
    kwargs.setdefault("today", TODAY)
    return extract_health(raw, **kwargs)


# ----------------------------------------------------------------------
# Cocientes
# ----------------------------------------------------------------------


def test_ratios_between_figures_of_the_same_report():
    h = health(AAPL)

    assert h.net_debt_to_ebitda == pytest.approx((84.3 - 62.4) / 168.0)
    assert h.fcf_margin == pytest.approx(107.7 / 466.8)
    assert h.fcf_yield == pytest.approx(107.7e9 / 4.9e12)
    assert h.operating_margin == pytest.approx(0.326)
    assert h.applies is True and not h.caveats


def test_a_currency_mismatch_keeps_ratios_but_drops_the_yield():
    """PBR: cotiza en USD y reporta en BRL. Los cocientes entre cifras del
    mismo reporte no se ven afectados; el rendimiento (caja en BRL sobre
    capitalización en USD) sí, y no debe calcularse."""
    raw = {**AAPL, "financialCurrency": "BRL"}
    h = health(raw)

    assert h.currency_mismatch is True
    assert h.net_debt_to_ebitda is not None and h.fcf_margin is not None
    assert h.fcf_yield is None
    assert "BRL" in h.unavailable["fcf_yield"] and "USD" in h.unavailable["fcf_yield"]
    assert any("reporta en BRL" in c for c in h.caveats)


def test_an_incoherent_market_cap_drops_the_yield():
    """Clases duales y ADR con ratio distinto de 1: capitalización ≠ precio ×
    acciones, y cualquier cociente que la use sería falso."""
    h = health({**AAPL, "sharesOutstanding": 3.0e9})     # 336 × 3.0e9 = 1,0e12 ≠ 4,9e12

    assert h.fcf_yield is None
    assert "no cuadra" in h.unavailable["fcf_yield"]
    # lo que no usa la capitalización se conserva
    assert h.fcf_margin is not None


def test_a_missing_field_is_none_never_zero():
    h = health({"financialCurrency": "USD", "totalRevenue": 100e9})

    assert h.net_debt_to_ebitda is None and h.free_cash_flow is None
    assert h.total_debt is None                            # «sin dato» ≠ «sin deuda»
    assert "net_debt_to_ebitda" in h.unavailable


def test_a_non_positive_ebitda_has_no_leverage_ratio():
    h = health({**AAPL, "ebitda": -5e9})

    assert h.net_debt_to_ebitda is None
    assert "EBITDA no positivo" in h.unavailable["net_debt_to_ebitda"]


def test_booleans_are_not_numbers():
    """isinstance(True, int) es verdadero: un booleano no puede pasar por cifra."""
    h = health({**AAPL, "totalDebt": True})

    assert h.total_debt is None


# ----------------------------------------------------------------------
# Bancos, inmobiliarias, brazos financieros
# ----------------------------------------------------------------------


def test_banks_have_no_debt_or_cash_metrics():
    """JPM: caja operativa de -162.000 M y EBITDA vacío. Calcular algo sería
    presentar un número sin sentido."""
    raw = {**AAPL, "operatingCashflow": -162e9, "payoutRatio": 0.26}
    h = health(raw, sector="Financial Services", industry="Banks - Diversified")

    assert h.applies is False
    assert "materia prima" in h.not_applicable_reason
    assert h.net_debt_to_ebitda is None and h.fcf_margin is None
    assert h.payout_ratio == pytest.approx(0.26)           # el reparto sí es legible


def test_a_payment_network_in_financial_services_is_not_treated_as_a_bank():
    h = health(AAPL, sector="Financial Services", industry="Credit Services")

    assert h.applies is True and h.net_debt_to_ebitda is not None


def test_real_estate_payout_is_not_reported():
    h = health({**AAPL, "payoutRatio": 2.36}, sector="Real Estate", industry="REIT - Retail")

    assert h.payout_ratio is None
    assert any("inmobiliaria" in c for c in h.caveats)


def test_captive_finance_is_warned_about():
    h = health(AAPL, sector="Consumer Cyclical", industry="Auto Manufacturers")

    assert h.is_captive_finance is True
    assert any("brazo financiero" in c for c in h.caveats)


def test_no_raw_payload_is_reported_not_invented():
    h = health(None)

    assert h.has_data is False and h.net_debt_to_ebitda is None
    assert h.caveats


# ----------------------------------------------------------------------
# Calendario
# ----------------------------------------------------------------------


def test_only_future_dates_are_reported():
    """AAPL: `earningsTimestamp` trae la última fecha (ya pasada), no la próxima."""
    past = health({"earningsTimestamp": ts(TODAY - dt.timedelta(days=52)),
                   "exDividendDate": ts(TODAY - dt.timedelta(days=41))})
    assert past.calendar.next_earnings is None and past.calendar.ex_dividend is None
    assert past.calendar.days_to_earnings is None

    future = health({"earningsTimestamp": ts(TODAY + dt.timedelta(days=9)),
                     "isEarningsDateEstimate": True,
                     "exDividendDate": ts(TODAY + dt.timedelta(days=10))})
    assert future.calendar.days_to_earnings == 9
    assert future.calendar.earnings_is_estimate is True
    assert future.calendar.days_to_ex_dividend == 10


def test_today_counts_as_upcoming():
    h = health({"earningsTimestamp": ts(TODAY)})

    assert h.calendar.days_to_earnings == 0


def test_the_estimate_flag_needs_a_date():
    h = health({"isEarningsDateEstimate": True})

    assert h.calendar.earnings_is_estimate is False


# ----------------------------------------------------------------------
# Analistas
# ----------------------------------------------------------------------


def test_the_analyst_upside_is_computed_and_labelled_as_opinion_data():
    h = health({"targetMeanPrice": 336.0 * 1.2, "numberOfAnalystOpinions": 30,
                "recommendationKey": "buy"})

    assert h.analyst.upside_pct == pytest.approx(0.2)
    assert h.analyst.analysts == 30 and h.analyst.recommendation == "buy"


def test_an_implausible_target_is_dropped():
    """Objetivo en otra moneda o con otra escala de ADR: no es opinión, es un
    dato roto, y no se enseña ni el objetivo."""
    h = health({"targetMeanPrice": 336.0 * 50, "numberOfAnalystOpinions": 5})

    assert h.analyst.target_mean is None and h.analyst.upside_pct is None


def test_recommendation_none_is_absent():
    h = health({"recommendationKey": "none"})

    assert h.analyst.recommendation is None
