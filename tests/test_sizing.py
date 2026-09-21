"""Tamaño de posición: la aritmética y sus salvaguardas."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.asset_class import AssetClass
from app.services.sizing import (
    STRESS_FLOOR_BY_CLASS,
    size_position,
    stress_floor,
)

PISO_ACCION = STRESS_FLOOR_BY_CLASS[AssetClass.ACCION]
PISO_FONDO = STRESS_FLOOR_BY_CLASS[AssetClass.FONDO_ACCIONES]


def size(**overrides):
    args = dict(
        max_drawdown=0.40, asset_class=AssetClass.ACCION,
        risk_budget_pct=2.0, max_position_pct=10.0,
    )
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
    result = size(
        max_drawdown=0.30, asset_class=AssetClass.FONDO_ACCIONES, max_position_pct=5.0
    )
    # 2% / 30% = 6.7% > tope 5%
    assert result.target_pct == 5.0
    assert result.binding == "cap"
    assert result.loss_if_repeats_pct_of_capital < 2.0


def test_a_calm_recent_year_does_not_inflate_the_weight():
    """El suelo evita dar un peso enorme a una acción que solo pareció tranquila."""
    result = size(max_drawdown=0.08)
    assert result.stress_loss_pct == pytest.approx(PISO_ACCION * 100)
    assert result.stress_source == "floor"
    assert result.observed_drawdown_pct == pytest.approx(8.0)
    assert any("suelo" in note for note in result.notes)


def test_funds_have_a_lower_floor_than_single_stocks():
    resultado = size(max_drawdown=0.05, asset_class=AssetClass.FONDO_ACCIONES)
    assert resultado.stress_loss_pct == pytest.approx(PISO_FONDO * 100)
    assert PISO_FONDO < PISO_ACCION


def test_crypto_is_not_sized_like_a_company():
    """El suelo del cripto está MEDIDO, no supuesto.

    Sobre los 25 criptos del universo con cinco años de histórico, la mediana
    de la caída máxima es del 85,3% (BTC 76,6%, ETH 79,4%, el peor 100%).
    Antes `stress_floor` solo distinguía «fondo» de «acción», así que una
    cripto se dimensionaba con el 35% de una empresa: un 2% de presupuesto
    daba un 5,7% de la cartera en vez del 2,4% que corresponde.
    """
    cripto = size(max_drawdown=0.30, asset_class=AssetClass.CRIPTO)
    accion = size(max_drawdown=0.30, asset_class=AssetClass.ACCION)

    assert cripto.stress_source == "floor"
    assert cripto.stress_loss_pct == pytest.approx(85.0)
    assert cripto.target_pct < accion.target_pct / 2
    assert "cripto" in " ".join(cripto.notes)


def test_what_cannot_be_classified_is_sized_most_conservatively():
    """No saber qué es algo no puede salir más barato que saberlo."""
    desconocido = stress_floor(AssetClass.DESCONOCIDO)
    assert desconocido >= max(
        STRESS_FLOOR_BY_CLASS[c]
        for c in (AssetClass.ACCION, AssetClass.FONDO_ACCIONES, AssetClass.RENTA_FIJA)
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


# ----------------------------------------------------------------------
# Un núcleo no es una apuesta
# ----------------------------------------------------------------------


def test_a_diversified_fund_says_the_cap_may_not_apply_to_it():
    """Decirle a alguien que su VT no puede pasar del 6,7% de la cartera es
    decirle que deje el 93% en efectivo.

    El método reparte un presupuesto de riesgo entre APUESTAS: cuánto puedes
    poner en algo que puede caer solo. Un fondo mundial no es una apuesta, es
    la base sobre la que se ponen las apuestas.
    """
    nucleo = size(max_drawdown=0.26, asset_class=AssetClass.FONDO_ACCIONES,
                  is_diversified=True)

    assert nucleo.applies_to == "core_or_single"
    nota = next(n for n in nucleo.notes if "NÚCLEO" in n)
    assert "plan de asignación" in nota, "Se dice DÓNDE se contesta esa pregunta"
    assert "Plan" in nota


def test_the_number_does_not_change_only_what_it_means():
    """Inventar un techo distinto para los fondos amplios sería fabricar un
    número que no mide nada."""
    suelta = size(max_drawdown=0.26, asset_class=AssetClass.FONDO_ACCIONES)
    nucleo = size(max_drawdown=0.26, asset_class=AssetClass.FONDO_ACCIONES,
                  is_diversified=True)

    assert suelta.target_pct == nucleo.target_pct
    assert suelta.stress_loss_pct == nucleo.stress_loss_pct
    assert suelta.applies_to == "single_position"


def test_percentages_carry_no_floating_point_noise():
    """`6.666666666666667%` viajaba tal cual por la API, y el asesor lo repite
    sin formatear."""
    # 2 / 30 = 6,666… El suelo de un fondo de acciones es 30%, así que la
    # caída observada del 30% no lo eleva y el cociente sale periódico.
    resultado = size(
        max_drawdown=0.30, asset_class=AssetClass.FONDO_ACCIONES, risk_budget_pct=2.0
    )
    assert resultado.stress_loss_pct == 30.0
    assert resultado.target_pct == 6.67
    assert resultado.add_pct == 6.67


def test_the_floor_note_reads_as_spanish():
    """Decía «un un fondo de acciones»: el artículo estaba dos veces."""
    resultado = size(max_drawdown=0.05, asset_class=AssetClass.FONDO_ACCIONES)
    nota = next(n for n in resultado.notes if "suelo" in n)
    assert "un un" not in nota
    assert "un fondo de acciones típica" in nota or "fondo de acciones" in nota
