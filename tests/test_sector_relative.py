"""Valoración relativa al sector.

El fallo que esto corrige: los múltiplos se ordenaban contra los 494 activos
mezclados, así que un banco a P/E 9 «ganaba» a una tecnológica a P/E 30 solo por
ser un banco. Estos tests fijan tres cosas: que el percentil dentro del sector
equipara sectores baratos y caros, que un sector con pocos pares NO se fía de sí
mismo, y que la calificación absoluta se ancla en la mediana del sector.
"""

from __future__ import annotations

import datetime as dt
from statistics import mean

import pytest

from app.core.config import settings
from app.models import Asset, AssetType, FundamentalSnapshot, Portfolio, PriceHistory
from app.services import opportunities as opportunity_service
from app.services.grading import assess
from app.services.regions import MarketRegion
from app.services.scoring import group_medians, grouped_percentile_ranks

# ----------------------------------------------------------------------
# Primitivas
# ----------------------------------------------------------------------


def test_grouped_ranks_put_a_cheap_and_an_expensive_sector_on_the_same_scale():
    values = [float(v) for v in range(1, 9)] + [float(v) for v in range(101, 109)]
    groups = ["Bancos"] * 8 + ["Tech"] * 8

    universe = grouped_percentile_ranks(values, [None] * 16, min_peers=8)
    by_sector = grouped_percentile_ranks(values, groups, min_peers=8)

    # Contra el universo, todos los bancos quedan por debajo de todas las tech.
    assert max(universe[:8]) < min(universe[8:])
    # Dentro de su sector, ambos sectores reciben exactamente la misma escala.
    assert by_sector[:8] == pytest.approx(by_sector[8:])
    assert mean(by_sector[:8]) == pytest.approx(50.0)


def test_a_sector_with_too_few_peers_falls_back_to_the_universe():
    """Con 3 empresas, «la más barata de su sector» sale con 83 puntos por no
    tener rivales. Ese grupo NO se ordena por dentro."""
    values = [1.0, 2.0, 3.0] + [float(v) for v in range(10, 18)]
    groups = ["Pocos"] * 3 + ["Muchos"] * 8

    universe = grouped_percentile_ranks(values, [None] * 11, min_peers=8)
    result = grouped_percentile_ranks(values, groups, min_peers=8)

    assert result[:3] == pytest.approx(universe[:3])
    assert result[3:] != pytest.approx(universe[3:])


def test_missing_values_and_ungrouped_assets_are_handled():
    values = [1.0, None, 3.0, 4.0]
    result = grouped_percentile_ranks(values, ["S", "S", None, None], min_peers=2)

    assert result[1] is None                       # ausente sigue ausente
    assert result[0] is not None and result[2] is not None


def test_group_medians_only_report_groups_with_enough_peers():
    values = [1.0, 2.0, 3.0, 4.0, 100.0, 7.0]
    groups = ["A", "A", "A", "A", "A", "B"]

    medians = group_medians(values, groups, min_peers=4)

    assert set(medians) == {"A"}
    assert medians["A"] == (3.0, 5)                # la mediana, no la media (22,0)


# ----------------------------------------------------------------------
# Calificación absoluta anclada en el sector
# ----------------------------------------------------------------------


def test_a_bank_is_a_bargain_against_the_market_but_normal_against_banks():
    """El caso de CIB: P/E 9,6 frente a un mercado de 24,6."""
    against_market = assess(trailing_pe=9.6, market_pe=24.6)
    against_banks = assess(
        trailing_pe=9.6, market_pe=24.6,
        sector_trailing_pe=10.0, sector_name="Financial Services",
    )

    valuation = lambda a: next(s for s in a.signals if s.name == "valuation")  # noqa: E731
    assert valuation(against_market).points == 2
    assert valuation(against_banks).points == 0
    assert "de su sector (Financial Services)" in valuation(against_banks).detail
    assert "del mercado" in valuation(against_market).detail
    assert valuation(against_banks).inputs["reference_pe"] == 10.0


def test_forward_pe_is_compared_with_the_sector_forward_pe():
    """Comparar un forward con la mediana TRAILING sesgaría a favor del forward,
    que casi siempre es menor."""
    result = assess(
        trailing_pe=-5.0, forward_pe=9.0, market_pe=25.0,
        sector_trailing_pe=14.0, sector_forward_pe=9.0, sector_name="Energy",
    )
    valuation = next(s for s in result.signals if s.name == "valuation")

    assert valuation.points == 0                    # 9 / 9 = en línea
    assert valuation.inputs["reference_pe"] == 9.0


def test_without_a_sector_reference_the_market_anchor_is_unchanged():
    result = assess(trailing_pe=14.0, market_pe=25.0)
    valuation = next(s for s in result.signals if s.name == "valuation")

    assert valuation.points == 2
    assert "del mercado" in valuation.detail


# ----------------------------------------------------------------------
# El motor completo
# ----------------------------------------------------------------------


def _add(db, symbol, sector, pe, *, asset_type=AssetType.STOCK, drift=1.002):
    asset = Asset(
        symbol=symbol, name=f"{symbol} SA", currency="USD",
        asset_type=asset_type, country="United States", sector=sector,
    )
    db.add(asset)
    db.flush()
    today = dt.date.today()
    price = 100.0
    for day in range(300):
        price *= drift
        db.add(PriceHistory(
            asset_id=asset.id, date=today - dt.timedelta(days=300 - day),
            close=price, adj_close=price,
        ))
    db.add(FundamentalSnapshot(
        asset_id=asset.id, as_of=today, trailing_pe=pe, forward_pe=pe * 0.9,
        return_on_equity=0.15, profit_margin=0.12, debt_to_equity=60.0,
        revenue_growth=0.08, fetched_at=dt.datetime.now(dt.UTC),
    ))
    return asset


@pytest.fixture
def two_sectors(db):
    """8 bancos baratos y 8 tecnológicas caras: dos sectores con múltiplos
    estructuralmente distintos."""
    assets = [
        _add(db, f"BK{i}", "Financial Services", 8.0 + i) for i in range(8)
    ] + [
        _add(db, f"TK{i}", "Technology", 30.0 + 3 * i) for i in range(8)
    ]
    portfolio = Portfolio(name="Sectores", base_currency="USD")
    db.add(portfolio)
    db.commit()
    return portfolio, assets


def _run(db, universe, **kwargs):
    portfolio, assets = universe
    return opportunity_service.compute_opportunities(
        db, portfolio, assets=assets, limit=50, **kwargs
    )


def _mean_value(rows, prefix):
    return mean(r.value.score for r in rows if r.symbol.startswith(prefix))


def test_a_cheap_sector_no_longer_wins_on_value_just_for_being_cheap(
    db, two_sectors, monkeypatch
):
    monkeypatch.setattr(settings, "opportunity_sector_neutral_value", False)
    old = _run(db, two_sectors).opportunities
    assert _mean_value(old, "BK") > _mean_value(old, "TK") + 30        # el sesgo

    opportunity_service.clear_cache()
    monkeypatch.setattr(settings, "opportunity_sector_neutral_value", True)
    new = _run(db, two_sectors).opportunities

    assert _mean_value(new, "BK") == pytest.approx(_mean_value(new, "TK"), abs=1.0)
    # Y dentro de cada sector sigue ganando el más barato.
    assert next(r for r in new if r.symbol == "BK0").value.score > next(
        r for r in new if r.symbol == "BK7"
    ).value.score


def test_rows_say_which_reference_they_were_ranked_against(db, two_sectors):
    rows = {r.symbol: r for r in _run(db, two_sectors).opportunities}

    bank = rows["BK3"]
    assert bank.value_basis == "sector"
    assert bank.value_reference == "Financial Services"
    assert bank.sector_peer_count == 8
    assert bank.sector_pe == pytest.approx(11.5)   # mediana de 8..15


def test_a_sector_with_few_peers_falls_back_to_the_class_and_says_so(db):
    """Sin pares de sector suficientes, la referencia es la CLASE.

    Antes el respaldo era el universo entero. Ya no existe: mezclar el P/E de
    una eléctrica con el de un fondo de bonos era justo lo que producía que un
    fondo con `trailingPE = 0,89` encabezara la valoración de los 490.
    """
    assets = [_add(db, f"BK{i}", "Financial Services", 8.0 + i) for i in range(8)]
    assets += [_add(db, f"UT{i}", "Utilities", 15.0 + i) for i in range(3)]
    portfolio = Portfolio(name="Pocos", base_currency="USD")
    db.add(portfolio)
    db.commit()

    rows = {r.symbol: r for r in _run(db, (portfolio, assets)).opportunities}

    assert rows["UT0"].value_basis == "class"
    assert rows["UT0"].value_reference == "Acciones"
    assert rows["BK0"].value_basis == "sector"


def test_etfs_never_join_a_sector_group(db):
    """Un ETF sectorial trae sector, pero su P/E es el de su cesta.

    Los cinco ETFs son el mínimo para que su clase se pueda ordenar por
    dentro; con menos quedaría fuera del ranking por otra razón y este test
    pasaría sin comprobar nada.
    """
    assets = [_add(db, f"BK{i}", "Financial Services", 10.0 + i) for i in range(8)]
    etfs = [
        _add(db, f"XL{i}", "Financial Services", 1.0 + i, asset_type=AssetType.ETF)
        for i in range(5)
    ]
    portfolio = Portfolio(name="ETF", base_currency="USD")
    db.add(portfolio)
    db.commit()

    rows = {r.symbol: r for r in _run(db, (portfolio, [*assets, *etfs])).opportunities}

    assert rows["XL0"].value_basis == "class", (
        "El ETF no entra en el grupo sectorial: su P/E es el de su cesta, "
        "así que se ordena contra otros fondos de acciones"
    )
    assert rows["XL0"].value_reference == "Fondos de acciones"
    assert rows["BK0"].sector_pe == pytest.approx(13.5)   # sin los P/E de los ETFs


def test_the_sector_reference_does_not_change_when_the_view_is_filtered(db, two_sectors):
    """Filtrar es una operación de vista: la referencia de una empresa no puede
    depender de qué pares se estén mostrando."""
    full = {r.symbol: r for r in _run(db, two_sectors).opportunities}
    filtered = {
        r.symbol: r for r in _run(db, two_sectors, regions={MarketRegion.US}).opportunities
    }

    assert filtered["BK0"].sector_pe == full["BK0"].sector_pe
    assert filtered["BK0"].value.score == full["BK0"].value.score
