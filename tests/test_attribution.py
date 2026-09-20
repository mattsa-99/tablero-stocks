"""Atribución de divisa y riesgo de la cartera.

`unrealized_pnl` mezclaba dos cosas que no se parecen: lo que hizo la empresa
y lo que hizo el cambio. Medido sobre SPY a un año, el índice subió un 16,6%
en dólares mientras quien mide en pesos perdía un 5,5%, porque el peso se
revaluó un 19%.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.models import (
    Asset,
    AssetQuote,
    AssetType,
    FxRateDaily,
    Portfolio,
    Transaction,
    TransactionType,
)
from app.services import portfolio as portfolio_service

HOY = dt.date.today()


def montar(db, *, precio_hoy: str, fx_compra: str, fx_hoy: str, precio_compra="100"):
    """Una compra en USD dentro de una cartera en COP."""
    cartera = Portfolio(name="COP", base_currency="COP")
    activo = Asset(symbol="AAPL", name="Apple", currency="USD",
                   asset_type=AssetType.STOCK, sector="Technology")
    db.add_all([cartera, activo])
    db.flush()
    db.add(AssetQuote(asset_id=activo.id, price=float(precio_hoy), currency="USD",
                      fetched_at=dt.datetime.now(dt.UTC), is_stale=False, source="test"))
    db.add(FxRateDaily(base_currency="USD", quote_currency="COP", date=HOY,
                       rate=Decimal(fx_hoy), source="test",
                       fetched_at=dt.datetime.now(dt.UTC)))
    db.add(Transaction(
        portfolio_id=cartera.id, asset_id=activo.id, type=TransactionType.BUY,
        executed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=30),
        quantity=Decimal("10"), price=Decimal(precio_compra), fees=Decimal("0"),
        currency="USD", fx_rate_to_base=Decimal(fx_compra),
    ))
    db.commit()
    return cartera


def test_the_two_effects_add_up_to_the_total_exactly(db):
    """No es una aproximación: es una identidad, y por eso se puede publicar.

    Si no sumaran el total, el desglose dejaría de ser verificable a ojo y
    habría que pedirle al usuario un acto de fe.
    """
    cartera = montar(db, precio_compra="100", precio_hoy="120", fx_compra="4000", fx_hoy="3000")
    resumen = portfolio_service.get_summary(db, cartera)

    assert resumen.asset_pnl + resumen.fx_pnl == resumen.unrealized_pnl


def test_a_good_pick_ruined_by_the_currency_shows_both(db):
    """EL caso que motivó todo esto.

    La acción sube un 20% y aun así la cartera pierde, porque el peso se
    revaluó un 25%. Sin separar, el usuario solo ve la pérdida y concluye que
    eligió mal.
    """
    cartera = montar(db, precio_compra="100", precio_hoy="120", fx_compra="4000", fx_hoy="3000")
    resumen = portfolio_service.get_summary(db, cartera)

    assert resumen.asset_pnl > 0, "el activo subió: el efecto activo es positivo"
    assert resumen.fx_pnl < 0, "el peso se revaluó: el efecto divisa es negativo"
    assert resumen.unrealized_pnl < 0, "y el neto es una pérdida"


def test_an_asset_in_the_base_currency_has_nothing_to_attribute(db):
    """Un cero invitaría a leer "la divisa no aportó" cuando no interviene."""
    cartera = Portfolio(name="USD", base_currency="USD")
    activo = Asset(symbol="MSFT", name="Microsoft", currency="USD",
                   asset_type=AssetType.STOCK, sector="Technology")
    db.add_all([cartera, activo])
    db.flush()
    db.add(AssetQuote(asset_id=activo.id, price=120.0, currency="USD",
                      fetched_at=dt.datetime.now(dt.UTC), is_stale=False, source="test"))
    db.add(Transaction(
        portfolio_id=cartera.id, asset_id=activo.id, type=TransactionType.BUY,
        executed_at=dt.datetime.now(dt.UTC), quantity=Decimal("1"),
        price=Decimal("100"), fees=Decimal("0"), currency="USD",
        fx_rate_to_base=Decimal("1"),
    ))
    db.commit()

    posicion = portfolio_service.get_summary(db, cartera).positions[0]
    assert posicion.asset_pnl is None
    assert posicion.fx_pnl is None
    assert posicion.average_fx_rate is None


def test_the_native_price_and_the_converted_one_are_both_published(db):
    """Un activo de la BVC cotiza en pesos: en una cartera en dólares hacen
    falta las dos cifras sin que el frontend multiplique un string."""
    cartera = montar(db, precio_compra="100", precio_hoy="120", fx_compra="4000", fx_hoy="3500")
    posicion = portfolio_service.get_summary(db, cartera).positions[0]

    assert posicion.current_price == Decimal("120")
    assert posicion.current_price_base == Decimal("120") * Decimal("3500")


def test_selling_leaves_the_average_rate_of_what_remains(db):
    """Vender no cambia el coste medio de lo que queda, tampoco el tipo medio.

    Los dos costes se vacían en la misma proporción; con cálculos
    independientes el redondeo los separaría y la atribución dejaría de cuadrar.
    """
    cartera = montar(db, precio_compra="100", precio_hoy="120", fx_compra="4000", fx_hoy="3500")
    activo = db.query(Asset).filter(Asset.symbol == "AAPL").one()
    db.add(Transaction(
        portfolio_id=cartera.id, asset_id=activo.id, type=TransactionType.SELL,
        executed_at=dt.datetime.now(dt.UTC), quantity=Decimal("4"),
        price=Decimal("120"), fees=Decimal("0"), currency="USD",
        fx_rate_to_base=Decimal("3500"),
    ))
    db.commit()

    resumen = portfolio_service.get_summary(db, cartera)
    posicion = resumen.positions[0]
    assert posicion.quantity == Decimal("6")
    assert posicion.average_fx_rate == Decimal("4000")
    assert posicion.average_cost_local == Decimal("100")
    assert resumen.asset_pnl + resumen.fx_pnl == resumen.unrealized_pnl


# ----------------------------------------------------------------------
# Riesgo de la cartera
# ----------------------------------------------------------------------


def test_concentration_is_reported(db):
    """El simulador ya medía esto y la pantalla principal no lo enseñaba."""
    cartera = montar(db, precio_compra="100", precio_hoy="120", fx_compra="4000", fx_hoy="3500")
    resumen = portfolio_service.get_summary(db, cartera)

    # Una sola posición: concentración máxima, diversificación nula.
    assert resumen.top_position_pct == Decimal("100")
    assert resumen.diversification_index == Decimal("0")


def test_risk_is_none_without_enough_history_instead_of_zero(db):
    """Un 0% de volatilidad diría "esta cartera no se mueve", que es falso."""
    cartera = montar(db, precio_compra="100", precio_hoy="120", fx_compra="4000", fx_hoy="3500")
    resumen = portfolio_service.get_summary(db, cartera)

    assert resumen.annualized_volatility_pct is None
    assert resumen.max_drawdown_pct is None
