"""Perfil de fondo: qué hay dentro, cuánto cuesta y quién debe el dinero.

Del payload de `funds_data` se guarda lo que resultó estable y se descarta lo
que se probó y no sirve. Estas pruebas fijan las dos mitades: que lo guardado
se use bien, y que lo descartado no vuelva por la puerta de atrás.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.models import Asset, AssetType, FundProfile
from app.providers.base import FundProfile as FundProfileData
from app.services import ficha as ficha_service
from app.services.asset_class import AssetClass, classify
from app.services.market_data import MarketDataService
from tests.fakes import FakeProvider


def fondo(db, symbol="XFND", **perfil) -> Asset:
    asset = Asset(symbol=symbol, currency="USD", asset_type=AssetType.ETF)
    db.add(asset)
    db.flush()
    if perfil:
        db.add(FundProfile(
            asset_id=asset.id, fetched_at=dt.datetime.now(dt.UTC), **perfil
        ))
    db.commit()
    db.refresh(asset)
    return asset


# ----------------------------------------------------------------------
# Qué hay dentro decide la clase
# ----------------------------------------------------------------------


def test_the_composition_classifies_when_the_category_is_missing(db):
    """Es el dato DURO: un fondo con el 98,7% en bonos es de renta fija.

    No depende de que el proveedor haya categorizado nada, y por eso cierra
    el hueco que dejaba la categoría: los fondos nuevos y los de la BVC.
    """
    bonos = fondo(db, "XBND", stock_position=0.0011, bond_position=0.9867)
    resultado = classify(bonos)

    assert resultado.asset_class is AssetClass.RENTA_FIJA
    assert resultado.is_assumed is False, "Es un dato, no una suposición"
    assert resultado.is_earnings_backed is False


def test_a_mixed_fund_is_not_forced_into_a_class(db):
    """Un 50/50 no es ninguna de las dos cosas, y forzarlo sería inventar."""
    mixto = fondo(db, "XMIX", stock_position=0.5, bond_position=0.5)
    resultado = classify(mixto)

    assert resultado.is_assumed is True, "Cae al supuesto, declarado"
    assert resultado.is_earnings_backed is False


def test_cash_alone_never_decides(db):
    """GSG aparece con el 100% en caja porque la garantía está en letras.

    Llamarlo «renta fija» sería exactamente al revés de lo que es: es una
    cesta de futuros sobre materias primas.
    """
    caja = fondo(db, "XCSH", cash_position=1.0, stock_position=0.0, bond_position=0.0)
    assert classify(caja).asset_class is not AssetClass.RENTA_FIJA


def test_a_declared_class_beats_everything(db):
    """Para lo que el proveedor no sabe clasificar.

    Yahoo no cubre la composición de los fondos de la BVC: `GXTESCOL.CL` es
    un ETF de deuda pública colombiana y llega sin categoría ni perfil.
    """
    tes = Asset(
        symbol="GXTESCOL.CL", currency="COP", asset_type=AssetType.ETF,
        declared_asset_class="renta_fija",
    )
    db.add(tes)
    db.commit()

    resultado = classify(tes)
    assert resultado.asset_class is AssetClass.RENTA_FIJA
    assert resultado.is_assumed is False, "Lo declaró una persona, no una heurística"
    assert "catálogo" in resultado.reason


# ----------------------------------------------------------------------
# El coste
# ----------------------------------------------------------------------


def test_the_expense_ratio_is_rounded_for_presentation(db):
    """Yahoo devuelve 0.00029999999 para el 0,03% de IVV.

    Sin redondear, la ficha enseñaría «0,029999999%». Se corrige en
    PRESENTACIÓN y no en el dato, igual que el P&L que redondea a cero.
    """
    ivv = fondo(db, "IVV", expense_ratio=0.00029999999, stock_position=0.9975)
    perfil = ficha_service._fund_profile(ivv, None)

    assert perfil.expense_ratio_pct == 0.03


def test_the_cost_is_also_shown_in_money(db):
    """Un 0,75% anual no se siente; «US$ 7,50 sobre los US$ 1.000 que te
    propone esta misma ficha» sí, y es la misma cifra."""
    from app.schemas.ficha import SizingRead

    caro = fondo(db, "XPNS", expense_ratio=0.0075, stock_position=0.99)
    sizing = SizingRead(
        risk_budget_pct=2.0, max_position_pct=10.0, asset_class="fondo_acciones",
        stress_loss_pct=30.0, stress_source="floor", target_pct=6.7,
        binding="risk", current_pct=0.0, add_pct=6.7,
        target_amount=Decimal("1000.00"), base_currency="USD",
        loss_if_repeats_pct_of_capital=2.0,
    )
    perfil = ficha_service._fund_profile(caro, sizing)

    assert perfil.expense_amount == Decimal("7.50")


# ----------------------------------------------------------------------
# Quién debe el dinero
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("symbol", "ratings", "esperado"),
    [
        # `us_government` SE SOLAPA con `aa`: sumarla contaría dos veces.
        ("TLT", {"aa": 1.0, "us_government": 0.996}, 100.0),
        ("TIP", {"aa": 0.9994, "us_government": 0.9994}, 99.9),
        ("AGG", {"aa": 0.7425, "aaa": 0.0221, "a": 0.1191, "bbb": 0.1162,
                 "bb": 0.0001, "us_government": 0.4908}, 100.0),
        # Un fondo de bonos basura: casi nada en grado de inversión.
        ("SJNK", {"bb": 0.5483, "aaa": 0.008, "other": 0.008, "b": 0.3341,
                  "bbb": 0.0053, "below_b": 0.0963}, 1.3),
    ],
)
def test_investment_grade_excludes_the_overlapping_label(db, symbol, ratings, esperado):
    """La frontera BBB/BB no es una convención más.

    Por debajo hay fondos de pensiones y mandatos que NO pueden comprar, así
    que en una crisis el comprador marginal desaparece justo cuando hace
    falta. Es la diferencia entre «paga más» y «paga más por una razón».
    """
    item = fondo(db, symbol, bond_position=0.98, credit_ratings=ratings)
    perfil = ficha_service._fund_profile(item, None)

    assert perfil.investment_grade_pct == pytest.approx(esperado, abs=0.1)


def test_an_equity_fund_has_no_credit_ratings(db):
    """Yahoo devuelve las once categorías en cero: guardarlas llenaría la
    ficha de filas vacías que no dicen nada."""
    acciones = fondo(db, "XEQ", stock_position=0.99, credit_ratings=None)
    perfil = ficha_service._fund_profile(acciones, None)

    assert perfil.credit_ratings is None
    assert perfil.investment_grade_pct is None


# ----------------------------------------------------------------------
# Lo que NO se guarda, y por qué
# ----------------------------------------------------------------------


def test_duration_and_sector_weights_are_deliberately_absent():
    """Se probaron y no son fiables; dejarlas fuera es parte del contrato.

    SJNK -de corto plazo- venía con duración 6,48 y TLT -el de 20+ años- con
    3,60: no es un factor de escala ni otra unidad, no hay patrón. Y los
    pesos por sector de SJNK dicen «comunicaciones 100%» en un fondo que es
    98,7% bonos.
    """
    campos = set(FundProfileData.__dataclass_fields__)
    assert not campos & {"duration", "maturity", "sector_weights", "credit_quality"}
    assert {"expense_ratio", "credit_ratings", "bond_position"} <= campos


# ----------------------------------------------------------------------
# El refresco
# ----------------------------------------------------------------------


def test_only_funds_are_asked_for_a_profile(db):
    """Preguntar por el perfil de una acción es una llamada garantizada a
    fallar, y hay 500 acciones en el universo."""
    accion = Asset(symbol="AAPL", currency="USD", asset_type=AssetType.STOCK)
    etf = Asset(symbol="IVV", currency="USD", asset_type=AssetType.ETF)
    db.add_all([accion, etf])
    db.commit()

    provider = FakeProvider(fund_profiles={
        "IVV": FundProfileData(symbol="IVV", category="Large Blend", expense_ratio=0.0003)
    })
    MarketDataService(db, provider).refresh_fund_profiles([accion, etf])

    llamadas = [c for c in provider.calls if c[0] == "fetch_fund_profiles"]
    assert llamadas, "Se pidió el perfil"
    assert llamadas[0][1][0] == ("IVV",), "Solo al fondo, nunca a la acción"


def test_not_being_a_fund_is_not_a_failure(db):
    """Yahoo tampoco cubre los fondos de la BVC.

    Apuntarlo contra el símbolo lo metería en un backoff exponencial por no
    ser algo que nadie afirmó que fuera. Se sella igual que un acierto para
    no volver a preguntar en treinta días.
    """
    from sqlalchemy import select

    from app.models import DataSyncState
    from app.providers.cache import ResourceType

    bvc = Asset(symbol="GXTESCOL.CL", currency="COP", asset_type=AssetType.ETF)
    db.add(bvc)
    db.commit()

    report = MarketDataService(db, FakeProvider()).refresh_fund_profiles([bvc])

    assert report.failed_symbols == []
    sello = db.scalar(select(DataSyncState).where(
        DataSyncState.resource_type == ResourceType.FUND_PROFILE
    ))
    assert sello is not None
    assert sello.last_success_at is not None
    assert sello.consecutive_failures == 0
