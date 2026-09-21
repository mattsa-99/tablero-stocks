"""Plan de asignación entre clases: la intención del usuario, no una medición.

Es la excepción declarada a «todo se calcula»: el ledger dice lo que hiciste,
no lo que querías, y un 40% en renta variable puede ser el plan o el resultado
de no haber rebalanceado en dos años. Esos dos casos piden acciones opuestas.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.core.exceptions import InvalidAllocationPlan
from app.models import (
    Asset,
    AssetQuote,
    AssetType,
    Portfolio,
    Transaction,
    TransactionType,
)
from app.services import allocation as allocation_service
from app.services import portfolio as portfolio_service
from tests.conftest import buy


@pytest.fixture
def cartera_api(db_of):
    """La misma cartera, pero en la base que usa el cliente HTTP."""
    return _construir(db_of)


@pytest.fixture
def cartera(db):
    return _construir(db)


def _construir(db) -> Portfolio:
    portfolio = Portfolio(name="Con plan", base_currency="USD")
    db.add(portfolio)

    fondo = Asset(
        symbol="IVV", currency="USD", asset_type=AssetType.ETF,
        fund_category="Large Blend",
    )
    accion = Asset(symbol="AAPL", currency="USD", sector="Technology")
    db.add_all([fondo, accion])
    db.flush()

    ayer = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
    db.add(buy(portfolio, fondo, ayer, qty="6", price="100", fx="1"))
    db.add(buy(portfolio, accion, ayer, qty="4", price="100", fx="1"))
    for asset in (fondo, accion):
        db.add(AssetQuote(
            asset_id=asset.id, price=100.0, currency="USD",
            fetched_at=dt.datetime.now(dt.UTC), is_stale=False, source="test",
        ))
    db.commit()
    return portfolio


def informe(db, portfolio):
    resumen = portfolio_service.get_summary(db, portfolio)
    return allocation_service.build_report(db, portfolio, resumen)


def test_without_a_plan_it_still_says_where_you_are(db, cartera):
    """Sin plan no hay desvío, pero el reparto actual sigue siendo información."""
    reporte = informe(db, cartera)

    assert reporte.has_plan is False
    por_clase = {p.asset_class: p for p in reporte.positions}
    assert por_clase["fondo_acciones"].status == "sin_plan"
    assert por_clase["fondo_acciones"].target_pct is None
    assert por_clase["fondo_acciones"].current_amount == Decimal("600")


def test_cash_counts_when_there_is_a_deposit(db, cartera):
    """Quien tiene la mitad en efectivo no está invertido del todo.

    Con las posiciones como denominador vería un 100% en renta variable y
    creería estar dentro del plan justo cuando más lejos está.
    """
    db.add(Transaction(
        portfolio_id=cartera.id, asset_id=None, type=TransactionType.DEPOSIT,
        executed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=2),
        cash_amount=Decimal("2000"), currency="USD", fx_rate_to_base=Decimal("1"),
    ))
    db.commit()

    reporte = informe(db, cartera)
    # 1.000 invertidos y 1.000 en caja: la mitad.
    assert reporte.total_value == Decimal("2000")
    por_clase = {p.asset_class: p for p in reporte.positions}
    assert por_clase["fondo_acciones"].current_pct == Decimal("30.00")
    assert not any("caja registrada es negativa" in a for a in reporte.warnings)


def test_a_negative_cash_balance_falls_back_and_says_so(db, cartera):
    """La caja puede ser negativa a propósito: se carga un histórico sin depósitos.

    Dividir por un `total_value` de cero o negativo daría porcentajes
    infinitos o con el signo cambiado. Se reparte sobre lo invertido y se
    declara, que es lo único afirmable con esos datos.
    """
    reporte = informe(db, cartera)

    suma = sum(p.current_amount for p in reporte.positions)
    assert suma == Decimal("1000")
    por_clase = {p.asset_class: p for p in reporte.positions}
    assert por_clase["fondo_acciones"].current_pct == Decimal("60.00")
    assert any("caja registrada es negativa" in a for a in reporte.warnings)


def test_a_plan_that_adds_to_more_than_a_hundred_is_refused(db, cartera):
    """Un plan que no se puede cumplir no es un plan."""
    with pytest.raises(InvalidAllocationPlan, match="más de 100"):
        allocation_service.set_targets(db, cartera, {
            "fondo_acciones": (Decimal("70"), Decimal("5")),
            "accion": (Decimal("50"), Decimal("5")),
        })


def test_a_class_that_cannot_be_planned_is_refused(db, cartera):
    """`derivado` no es accionable y `desconocido` no es una decisión."""
    with pytest.raises(InvalidAllocationPlan, match="no admite un objetivo"):
        allocation_service.set_targets(
            db, cartera, {"derivado": (Decimal("10"), Decimal("5"))}
        )


def test_the_band_is_what_separates_noise_from_a_decision(db, cartera):
    """Sin tolerancia, cualquier movimiento del mercado «obliga» a rebalancear.

    La cartera tiene 60% en fondos. Con objetivo 55 y banda 10, está dentro;
    con la misma desviación y banda 2, está fuera. Es el mismo número: lo que
    cambia es cuándo se decide que importa.
    """
    allocation_service.set_targets(db, cartera, {
        "fondo_acciones": (Decimal("55"), Decimal("10")),
        "accion": (Decimal("45"), Decimal("10")),
    })
    holgada = {p.asset_class: p for p in informe(db, cartera).positions}
    assert holgada["fondo_acciones"].status == "dentro"
    assert holgada["fondo_acciones"].drift_pct == Decimal("5.00")

    allocation_service.set_targets(db, cartera, {
        "fondo_acciones": (Decimal("55"), Decimal("2")),
        "accion": (Decimal("45"), Decimal("2")),
    })
    estrecha = {p.asset_class: p for p in informe(db, cartera).positions}
    assert estrecha["fondo_acciones"].status == "por_encima"
    assert estrecha["fondo_acciones"].drift_pct == Decimal("5.00")


def test_a_contribution_goes_to_the_most_lagging_class_and_never_sells(db, cartera):
    """Rebalancear con aportes evita realizar ganancias y pagar comisiones."""
    allocation_service.set_targets(db, cartera, {
        "fondo_acciones": (Decimal("40"), Decimal("5")),
        "accion": (Decimal("30"), Decimal("5")),
        "renta_fija": (Decimal("30"), Decimal("5")),
    })
    reporte = informe(db, cartera)
    orden = allocation_service.suggest_contribution(reporte, Decimal("500"))

    assert [p.asset_class for p in orden] == ["renta_fija"], (
        "Solo la clase que está POR DEBAJO recibe aporte"
    )
    assert all(p.gap_amount > 0 for p in orden), "Nunca se propone vender"
    # Los fondos están por encima y no aparecen: la desviación se informa,
    # pero la respuesta no es deshacer.
    exceso = next(p for p in reporte.positions if p.asset_class == "fondo_acciones")
    assert exceso.status == "por_encima"
    assert exceso not in orden


def test_a_partial_plan_says_how_much_it_covers(db, cartera):
    """Un plan que suma 70% deja un 30% sin decidir, y eso hay que decirlo."""
    allocation_service.set_targets(db, cartera, {
        "fondo_acciones": (Decimal("40"), Decimal("5")),
        "accion": (Decimal("30"), Decimal("5")),
    })
    reporte = informe(db, cartera)

    assert reporte.planned_pct == Decimal("70")
    assert any("70" in aviso for aviso in reporte.warnings)


def test_the_plan_is_replaced_whole_not_merged(db, cartera):
    """Enviar una clase y dejar las demás produciría sumas que nadie eligió."""
    allocation_service.set_targets(db, cartera, {
        "fondo_acciones": (Decimal("60"), Decimal("5")),
        "accion": (Decimal("40"), Decimal("5")),
    })
    allocation_service.set_targets(db, cartera, {
        "renta_fija": (Decimal("100"), Decimal("5")),
    })
    objetivos = {t.asset_class for t in allocation_service.get_targets(db, cartera)}
    assert objetivos == {"renta_fija"}


def test_the_plan_page_knows_every_plannable_class():
    """Añadir una clase planificable solo en Python la dejaría sin fila.

    El JS mantiene su propia copia -no hay build que la genere-, así que la
    única defensa contra la divergencia es comprobarlo aquí. Misma guarda que
    ya existe para las regiones y para las clases del ranking.
    """
    import re
    from pathlib import Path

    fuente = Path("app/static/js/plan.js").read_text(encoding="utf-8")
    bloque = re.search(r"const PLANNABLE = \[(.*?)\];", fuente, re.S)
    assert bloque, "No se encontró PLANNABLE en plan.js"
    en_js = set(re.findall(r'key:\s*"(\w+)"', bloque.group(1)))

    en_python = {c.value for c in allocation_service.PLANNABLE_CLASSES}
    assert en_js == en_python, (
        f"Solo en JS: {en_js - en_python}; solo en Python: {en_python - en_js}"
    )


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------


def test_the_api_refuses_a_plan_over_a_hundred(client, cartera_api):
    respuesta = client.put(
        f"/api/allocation?portfolio_id={cartera_api.id}",
        json={"targets": [
            {"asset_class": "accion", "target_pct": 70},
            {"asset_class": "renta_fija", "target_pct": 50},
        ]},
    )
    assert respuesta.status_code == 422
    assert "más de 100" in respuesta.json()["detail"]


def test_the_api_round_trips_a_plan(client, cartera_api):
    guardado = client.put(
        f"/api/allocation?portfolio_id={cartera_api.id}",
        json={"targets": [
            {"asset_class": "fondo_acciones", "target_pct": 60, "band_pct": 5},
            {"asset_class": "accion", "target_pct": 40, "band_pct": 5},
        ]},
    )
    assert guardado.status_code == 200
    assert guardado.json()["has_plan"] is True

    leido = client.get(f"/api/allocation?portfolio_id={cartera_api.id}&contribution=500")
    assert leido.status_code == 200
    cuerpo = leido.json()
    por_clase = {p["asset_class"]: p for p in cuerpo["positions"]}
    assert Decimal(por_clase["fondo_acciones"]["target_pct"]) == Decimal("60")
    assert "No es asesoramiento financiero" in cuerpo["disclaimer"]


def test_an_unknown_class_is_refused_by_the_api(client, cartera_api):
    respuesta = client.put(
        f"/api/allocation?portfolio_id={cartera_api.id}",
        json={"targets": [{"asset_class": "inventada", "target_pct": 10}]},
    )
    assert respuesta.status_code == 422
