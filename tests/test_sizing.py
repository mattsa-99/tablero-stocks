"""Tamaño de posición: la aritmética y sus salvaguardas."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.sizing import (
    STRESS_FLOOR_FUND,
    STRESS_FLOOR_STOCK,
    size_position,
)


def size(**overrides):
    args = dict(max_drawdown=0.40, is_fund=False, risk_budget_pct=2.0, max_position_pct=10.0)
    args.update(overrides)
    return size_position(**args)


def test_weight_is_risk_budget_over_stress_loss():
    # 2% / 40% = 5% de la cartera.
    result = size()
    assert result.target_pct == pytest.approx(5.0)
    assert result.binding == "risk"
    assert result.stress_source == "observed"


def test_losing_the_worst_fall_costs_exactly_the_risk_budget():
    """La garantía del método: al peso sugerido, repetir la peor caída pierde
    justo el presupuesto (mientras no mande el tope)."""
    result = size(max_drawdown=0.50, risk_budget_pct=2.0)
    assert result.loss_if_repeats_pct_of_capital == pytest.approx(2.0)


def test_cap_binds_for_calm_assets_and_loss_is_below_budget():
    result = size(max_drawdown=0.30, is_fund=True, max_position_pct=5.0)
    # 2% / 30% = 6.7% > tope 5%
    assert result.target_pct == 5.0
    assert result.binding == "cap"
    assert result.loss_if_repeats_pct_of_capital < 2.0


def test_a_calm_recent_year_does_not_inflate_the_weight():
    """El suelo evita dar un peso enorme a una acción que solo pareció tranquila."""
    result = size(max_drawdown=0.08)
    assert result.stress_loss_pct == pytest.approx(STRESS_FLOOR_STOCK * 100)
    assert result.stress_source == "floor"
    assert result.observed_drawdown_pct == pytest.approx(8.0)
    assert any("suelo" in note for note in result.notes)


def test_funds_have_a_lower_floor_than_single_stocks():
    assert size(max_drawdown=0.05, is_fund=True).stress_loss_pct == pytest.approx(
        STRESS_FLOOR_FUND * 100
    )


def test_no_history_uses_the_floor_and_says_so():
    result = size(max_drawdown=None)
    assert result.stress_source == "floor"
    assert result.observed_drawdown_pct is None
    assert any("No hay histórico" in note for note in result.notes)


def test_only_the_missing_part_is_added_when_you_already_hold_some():
    result = size(max_drawdown=0.40, current_weight_pct=3.0)
    assert result.target_pct == pytest.approx(5.0)
    assert result.add_pct == pytest.approx(2.0)


def test_nothing_is_added_when_already_at_or_over_target():
    result = size(max_drawdown=0.40, current_weight_pct=8.0)
    assert result.add_pct == 0.0
    assert any("no conviene añadir" in note for note in result.notes)


def test_amounts_follow_the_percentages():
    result = size(max_drawdown=0.40, capital=Decimal("10000000"), current_weight_pct=2.0)
    assert result.target_amount == Decimal("500000.00")
    assert result.add_amount == Decimal("300000.00")
    assert result.loss_if_repeats_amount == Decimal("200000.00")


def test_without_capital_only_percentages_are_returned():
    result = size()
    assert result.capital is None
    assert result.target_amount is None
    assert result.loss_if_repeats_pct_of_capital == pytest.approx(2.0)


def test_zero_or_negative_capital_gives_no_amounts():
    assert size(capital=Decimal("0")).target_amount is None


def test_foreign_currency_adds_a_warning():
    assert any("otra divisa" in n for n in size(currency_differs=True).notes)
    assert not any("otra divisa" in n for n in size(currency_differs=False).notes)


@pytest.mark.parametrize("budget,cap", [(0, 10), (-1, 10), (2, 0)])
def test_invalid_limits_are_rejected(budget, cap):
    with pytest.raises(ValueError):
        size(risk_budget_pct=budget, max_position_pct=cap)
