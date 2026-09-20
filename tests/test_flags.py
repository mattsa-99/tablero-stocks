"""Banderas de compra y veredicto.

Lo que más importa aquí no es que cada bandera salga, sino tres propiedades:
que el semáforo sea ASIMÉTRICO (una roja basta para el rojo, y el verde exige
ausencia de amarillas), que NUNCA diga «compra», y que las excepciones
sectoriales (bancos, utilities, brazos financieros) eviten falsos positivos que
enseñarían a ignorar las banderas.
"""

from __future__ import annotations

import datetime as dt

from app.schemas.opportunity import (
    AbsoluteAssessment,
    FactorDetail,
    OpportunityRead,
    SignalDetail,
)
from app.services.flags import build_flags, summarize
from app.services.health import CalendarInfo, HealthSnapshot

TODAY = dt.date(2026, 9, 20)


def factor(weight, **inputs):
    return FactorDetail(score=50, weight=weight, contribution=50 * weight,
                        available=True, inputs=inputs)


def valuation_signal(ratio=1.0, points=0, detail="en línea"):
    inputs = {"trailing_pe": 15.0, "reference_pe": 15.0 / ratio if ratio else None}
    if ratio is not None:
        inputs["pe_vs_reference"] = ratio
    return SignalDetail(name="valuation", label="Valoración", points=points,
                        detail=detail, inputs=inputs)


def op(**overrides) -> OpportunityRead:
    """Una fila de ranking neutra: nada dispara ninguna bandera por defecto."""
    grade = overrides.pop("grade", "B")
    labels = {"A": "Muy buena", "B": "Buena", "C": "Normal", "D": "Mala",
              "E": "Muy mala", "SIN_CALIFICAR": "Sin calificar"}
    base = dict(
        rank=50, symbol="TEST", name="Test SA", sector="Healthcare", currency="USD",
        market_region="US", score=60.0, baseline=20.0,
        value=factor(0.35), diversification=factor(0.15, sector_weight=0.0),
        momentum=factor(0.30, momentum_12_1=0.10, sma50_over_sma200_minus_1=0.03),
        risk=factor(-0.20, annualized_volatility=0.22, max_drawdown=0.15, beta=1.0),
        current_price=100.0, exposure_bucket="Healthcare", sector_weight_pct=0.0,
        assessment=AbsoluteAssessment(
            grade=grade, label=labels[grade], points=4, max_points=8,
            signals=[valuation_signal()],
        ),
        data_completeness=1.0, confidence="high",
    )
    base.update(overrides)
    return OpportunityRead(**base)


def health(**kwargs) -> HealthSnapshot:
    kwargs.setdefault("reporting_currency", "USD")
    kwargs.setdefault("quote_currency", "USD")
    return HealthSnapshot(has_data=True, **kwargs)


def codes(flags):
    return {f.code for f in flags}


def level_of(flags, code):
    return next(f.level for f in flags if f.code == code)


def build(o=None, h=None, **kwargs):
    return build_flags(o or op(), h or health(), **kwargs)


# ----------------------------------------------------------------------
# Calidad
# ----------------------------------------------------------------------


def test_a_bad_grade_is_red_and_a_good_one_is_green():
    assert level_of(build(op(grade="D")), "grade_bad") == "red"
    assert level_of(build(op(grade="E")), "grade_bad") == "red"
    assert level_of(build(op(grade="B")), "grade_good") == "green"
    assert level_of(build(op(grade="C")), "grade_mediocre") == "yellow"
    assert level_of(build(op(grade="SIN_CALIFICAR")), "grade_unrated") == "yellow"


def test_leading_the_ranking_without_being_good_is_called_out():
    """El caso ETB.CL: puesto 2 de 490 con calificación «Mala»."""
    flags = build(op(rank=2, grade="D"), universe_size=490)
    assert "rank_vs_grade" in codes(flags)

    assert "rank_vs_grade" not in codes(build(op(rank=2, grade="B"), universe_size=490))
    assert "rank_vs_grade" not in codes(build(op(rank=300, grade="D"), universe_size=490))


# ----------------------------------------------------------------------
# Valoración
# ----------------------------------------------------------------------


def _with_valuation(ratio, **extra):
    a = AbsoluteAssessment(
        grade="B", label="Buena", points=4, max_points=8,
        signals=[valuation_signal(ratio)],
    )
    return op(assessment=a, **extra)


def test_valuation_flags_follow_the_ratio_to_the_reference():
    assert level_of(build(_with_valuation(2.0)), "valuation_very_expensive") == "red"
    assert level_of(build(_with_valuation(1.5)), "valuation_expensive") == "yellow"
    assert level_of(build(_with_valuation(0.5)), "valuation_cheap") == "green"
    neutral = codes(build(_with_valuation(1.0)))
    assert not {"valuation_very_expensive", "valuation_expensive", "valuation_cheap"} & neutral


def test_the_valuation_flag_names_its_reference():
    sector = _with_valuation(2.0, value_basis="sector", value_reference="Financial Services")
    flag = next(f for f in build(sector) if f.code == "valuation_very_expensive")

    assert "Financial Services" in flag.title
    assert flag.evidence["pe_vs_reference"] == 2.0
    assert "mercado" in next(
        f for f in build(_with_valuation(2.0)) if f.code == "valuation_very_expensive"
    ).title


def test_a_loss_making_company_is_red_not_cheap():
    a = AbsoluteAssessment(
        grade="D", label="Mala", points=-2, max_points=6,
        signals=[SignalDetail(name="valuation", label="Valoración", points=-1,
                              detail="La empresa no tiene beneficios positivos",
                              inputs={"trailing_pe": -8.0})],
    )
    flags = build(op(assessment=a))

    assert level_of(flags, "no_earnings") == "red"
    assert "valuation_cheap" not in codes(flags)


def test_cyclical_sector_turns_yellow_only_when_the_stock_looks_cheap():
    cheap = _with_valuation(0.5, sector="Energy")
    assert level_of(build(cheap), "cyclical_sector") == "yellow"

    plain = op(sector="Energy")
    assert level_of(build(plain), "cyclical_sector") == "info"
    assert "cyclical_sector" not in codes(build(op(sector="Healthcare")))


# ----------------------------------------------------------------------
# Precio y riesgo
# ----------------------------------------------------------------------


def test_a_big_run_up_is_flagged_but_not_as_red():
    o = op(momentum=factor(0.30, momentum_12_1=0.98, sma50_over_sma200_minus_1=0.22))
    assert level_of(build(o), "already_ran_up") == "yellow"       # el caso CIB: +98%

    calm = op(momentum=factor(0.30, momentum_12_1=0.30, sma50_over_sma200_minus_1=0.05))
    assert "already_ran_up" not in codes(build(calm))


def test_a_sustained_fall_is_flagged():
    o = op(momentum=factor(0.30, momentum_12_1=-0.35, sma50_over_sma200_minus_1=-0.10))
    assert level_of(build(o), "falling") == "yellow"

    dip = op(momentum=factor(0.30, momentum_12_1=-0.35, sma50_over_sma200_minus_1=0.02))
    assert "falling" not in codes(build(dip))


def test_volatility_and_drawdown_have_two_levels():
    high = op(risk=factor(-0.20, annualized_volatility=0.40, max_drawdown=0.38, beta=1.0))
    assert level_of(build(high), "high_volatility") == "yellow"
    assert level_of(build(high), "deep_drawdown") == "yellow"

    extreme = op(risk=factor(-0.20, annualized_volatility=0.62, max_drawdown=0.55, beta=1.0))
    assert level_of(build(extreme), "extreme_volatility") == "red"
    assert level_of(build(extreme), "extreme_drawdown") == "red"

    calm = codes(build(op()))
    assert not {"high_volatility", "extreme_volatility", "deep_drawdown"} & calm


# ----------------------------------------------------------------------
# Deuda y caja
# ----------------------------------------------------------------------


def test_leverage_thresholds_depend_on_the_sector():
    ordinary = op(sector="Healthcare")
    moderate = build(ordinary, health(net_debt_to_ebitda=3.5))
    assert level_of(moderate, "leverage_elevated") == "yellow"
    severe = build(ordinary, health(net_debt_to_ebitda=5.0))
    assert level_of(severe, "leverage_high") == "red"

    # Una utility a 5,4x es lo normal: NO debe salir en rojo (enseñaría a
    # ignorar la bandera), y a 7,35x (NEE) sí.
    utility = op(sector="Utilities")
    assert level_of(build(utility, health(net_debt_to_ebitda=5.4)), "leverage_elevated") == "yellow"
    assert level_of(build(utility, health(net_debt_to_ebitda=7.35)), "leverage_high") == "red"
    assert "leverage_elevated" not in codes(build(utility, health(net_debt_to_ebitda=3.5)))


def test_captive_finance_softens_the_leverage_flag():
    """TM: 5,5x incluye la deuda de sus préstamos a clientes."""
    auto = op(sector="Consumer Cyclical")
    flag = next(f for f in build(auto, health(net_debt_to_ebitda=5.46, is_captive_finance=True))
                if f.code == "leverage_high")

    assert flag.level == "yellow" and "brazo financiero" in flag.detail


def test_net_cash_is_green():
    flags = build(h=health(net_debt_to_ebitda=-0.4))
    assert level_of(flags, "net_cash") == "green"


def test_negative_ebitda_with_net_debt_is_red():
    flags = build(h=health(ebitda=-1e9, total_debt=5e9, total_cash=1e9))
    assert level_of(flags, "negative_ebitda") == "red"

    no_debt = build(h=health(ebitda=-1e9, total_debt=1e9, total_cash=5e9))
    assert "negative_ebitda" not in codes(no_debt)


def test_cash_flow_flags():
    burning = build(h=health(operating_cash_flow=-2e9, free_cash_flow=-3e9))
    assert level_of(burning, "no_operating_cash") == "red"

    investing = build(h=health(operating_cash_flow=10e9, free_cash_flow=-2e9, fcf_margin=-0.06))
    assert level_of(investing, "fcf_negative") == "yellow"

    healthy = build(h=health(operating_cash_flow=10e9, free_cash_flow=6e9, fcf_margin=0.17))
    assert level_of(healthy, "fcf_positive") == "green"


def test_current_ratio_is_normal_low_in_utilities_only():
    assert level_of(build(op(sector="Energy"), health(current_ratio=0.85)),
                    "current_ratio_low") == "yellow"
    assert "current_ratio_low" not in codes(
        build(op(sector="Utilities"), health(current_ratio=0.53))
    )


def test_a_payout_above_one_is_yellow():
    assert level_of(build(h=health(payout_ratio=1.4)), "payout_high") == "yellow"
    assert "payout_high" not in codes(build(h=health(payout_ratio=0.5)))


def test_banks_get_an_explanation_instead_of_metrics():
    bank = HealthSnapshot(has_data=True, applies=False, not_applicable_reason="Un banco…")
    flags = build(op(sector="Financial Services"), bank)

    assert level_of(flags, "financial_metrics_na") == "info"
    assert not {"leverage_high", "leverage_elevated", "fcf_negative", "net_cash"} & codes(flags)


# ----------------------------------------------------------------------
# Calendario y datos
# ----------------------------------------------------------------------


def test_earnings_soon_and_ex_dividend_soon():
    soon = build(h=health(calendar=CalendarInfo(
        next_earnings=TODAY + dt.timedelta(days=5), earnings_is_estimate=True,
        days_to_earnings=5, ex_dividend=TODAY + dt.timedelta(days=10),
        days_to_ex_dividend=10)))
    flag = next(f for f in soon if f.code == "earnings_soon")

    assert flag.level == "yellow" and "estimada" in flag.title
    assert level_of(soon, "ex_dividend_soon") == "info"

    far = build(h=health(calendar=CalendarInfo(
        next_earnings=TODAY + dt.timedelta(days=40), days_to_earnings=40)))
    assert "earnings_soon" not in codes(far)


def test_stale_prices_and_incomplete_data():
    stale = build(op(notes=["Histórico detenido el 2026-08-01 (50 días): no se puntúan"],
                     data_completeness=0.33))
    assert level_of(stale, "stale_prices") == "red"
    assert "low_completeness" not in codes(stale)             # no se duplica el aviso

    partial = build(op(data_completeness=0.67))
    assert level_of(partial, "low_completeness") == "yellow"

    assert level_of(build(op(notes=["Sin datos fundamentales"])), "no_fundamentals") == "yellow"


def test_foreign_business_and_reporting_currency_are_context():
    colombian = op(market_region="COL", currency="USD")
    flags = build(colombian, health(currency_mismatch=True, reporting_currency="COP"))

    assert level_of(flags, "foreign_business_fx") == "info"
    assert level_of(flags, "reporting_currency_differs") == "info"
    assert "foreign_business_fx" not in codes(build(op(market_region="US")))
    assert "foreign_business_fx" not in codes(build(op(market_region="COL", currency="COP")))


# ----------------------------------------------------------------------
# Cartera
# ----------------------------------------------------------------------


def test_an_empty_portfolio_is_disclosed_and_a_concentrated_one_is_flagged():
    assert level_of(build(portfolio_position_count=0), "empty_portfolio") == "info"

    concentrated = op(sector_weight_pct=47.0, exposure_bucket="Technology")
    flag = next(f for f in build(concentrated, portfolio_position_count=3)
                if f.code == "sector_concentration")
    assert flag.level == "yellow" and "47%" in flag.title

    fine = op(sector_weight_pct=10.0)
    assert "sector_concentration" not in codes(build(fine, portfolio_position_count=3))
    assert "empty_portfolio" not in codes(build(fine, portfolio_position_count=3))


def test_the_broker_check_is_always_there():
    assert "verify_ticker" in codes(build())


# ----------------------------------------------------------------------
# Veredicto
# ----------------------------------------------------------------------


def test_one_red_flag_is_enough_for_a_red_verdict():
    flags = build(op(grade="D"))
    verdict = summarize(flags)

    assert verdict.level == "red" and verdict.red >= 1


def test_green_needs_no_yellow_and_no_red():
    clean = summarize(build(op(grade="A")))
    assert clean.level == "green"

    concentrated = op(grade="A", sector_weight_pct=60.0)
    with_yellow = summarize(build(concentrated, portfolio_position_count=2))
    assert with_yellow.level == "yellow"


def test_the_verdict_never_says_buy():
    for o in (op(grade="A"), op(grade="C"), op(grade="D")):
        verdict = summarize(build(o))
        assert "compra" not in verdict.label.lower()
        assert "NO es una recomendación de compra" in verdict.disclaimer


def test_counts_add_up():
    flags = build(op(grade="C"))
    verdict = summarize(flags)

    assert verdict.red + verdict.yellow + verdict.green + verdict.info == len(flags)
