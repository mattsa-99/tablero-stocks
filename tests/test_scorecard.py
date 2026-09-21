"""Foto del ranking y validación hacia adelante.

No es un backtest y el módulo lo dice en su propia API (`is_conclusive` es
siempre False). Lo que estas pruebas fijan es que la foto guarde lo que hace
falta para poder mirar atrás algún día, y que la comparación se haga DENTRO
de cada clase y contra un índice de su clase.
"""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.models import (
    Asset,
    AssetQuote,
    FundamentalSnapshot,
    Portfolio,
    PriceHistory,
    RankingSnapshot,
)
from app.services import opportunities as opp
from app.services import scorecard as scorecard_service


@pytest.fixture
def universo(db):
    """Seis acciones con trayectorias distintas y precio del día de la foto."""
    hoy = dt.date.today()
    spec = [
        ("SUBE1", 1.004, 10.0), ("SUBE2", 1.003, 12.0), ("SUBE3", 1.002, 14.0),
        ("BAJA1", 0.998, 40.0), ("BAJA2", 0.997, 45.0), ("BAJA3", 0.996, 50.0),
    ]
    creados = []
    for simbolo, deriva, pe in spec:
        item = Asset(symbol=simbolo, currency="USD", sector="Technology", is_universe=True)
        db.add(item)
        db.flush()
        precio = 100.0
        for dia in range(400):
            precio *= deriva
            db.add(PriceHistory(
                asset_id=item.id, date=hoy - dt.timedelta(days=400 - dia),
                close=precio, adj_close=precio,
            ))
        db.add(FundamentalSnapshot(
            asset_id=item.id, as_of=hoy, trailing_pe=pe,
            return_on_equity=0.2, profit_margin=0.15, debt_to_equity=30.0,
            revenue_growth=0.12, fetched_at=dt.datetime.now(dt.UTC),
        ))
        db.add(AssetQuote(
            asset_id=item.id, price=precio, currency="USD",
            fetched_at=dt.datetime.now(dt.UTC), is_stale=False, source="test",
        ))
        creados.append(item)
    portfolio = Portfolio(name="Tarjeta", base_currency="USD")
    db.add(portfolio)
    db.commit()
    return portfolio, creados


def test_the_snapshot_freezes_the_whole_ranking_not_the_visible_rows(db, universo):
    """`opportunities` viene recortado por `limit` y filtrado por la vista.

    Guardar eso haría que la foto dependiera de qué estaba mirando el usuario
    al capturarla, que es justo lo que la invalidaría como registro.
    """
    portfolio, assets = universo
    respuesta = opp.compute_opportunities(db, portfolio, assets=assets, limit=2)
    assert len(respuesta.opportunities) == 2

    scored = opp._scored_universe(db, portfolio, assets)
    guardadas = scorecard_service.capture(db, portfolio, rows=list(scored.rows))
    db.commit()

    assert guardadas == len(assets)
    filas = db.scalars(select(RankingSnapshot)).all()
    assert {f.symbol for f in filas} == {a.symbol for a in assets}


def test_the_snapshot_keeps_the_price_because_the_series_may_not(db, universo):
    """El precio guardado es lo que permite medir sin depender del histórico."""
    portfolio, assets = universo
    scored = opp._scored_universe(db, portfolio, assets)
    scorecard_service.capture(db, portfolio, rows=list(scored.rows))
    db.commit()

    fila = db.scalars(
        select(RankingSnapshot).where(RankingSnapshot.symbol == "SUBE1")
    ).one()
    assert fila.price is not None and fila.price > 0
    assert fila.currency == "USD"
    assert fila.asset_class == "accion"
    assert fila.class_size == len(assets)


def test_capturing_twice_the_same_day_does_not_duplicate(db, universo):
    """La clave única (fecha, cartera, símbolo) lo garantiza.

    Se cuentan FILAS y no el valor devuelto: `insert_ignore_duplicates`
    documenta que devuelve el tamaño del lote como cota superior cuando el
    driver no informa un `rowcount` fiable, que es el caso de SQLite con
    executemany.
    """
    portfolio, assets = universo
    scored = opp._scored_universe(db, portfolio, assets)
    scorecard_service.capture(db, portfolio, rows=list(scored.rows))
    db.commit()
    scorecard_service.capture(db, portfolio, rows=list(scored.rows))
    db.commit()

    assert len(db.scalars(select(RankingSnapshot)).all()) == len(assets)


def test_a_capture_is_weekly_not_daily(db, universo):
    """La calificación cambia de escalón muy despacio.

    Una foto diaria multiplicaría las filas por siete sin añadir información
    sobre lo que se quiere validar.
    """
    portfolio, assets = universo
    assert scorecard_service.is_capture_due(db, portfolio) is True

    scored = opp._scored_universe(db, portfolio, assets)
    scorecard_service.capture(db, portfolio, rows=list(scored.rows))
    db.commit()

    assert scorecard_service.is_capture_due(db, portfolio) is False
    futuro = dt.date.today() + dt.timedelta(days=7)
    assert scorecard_service.is_capture_due(db, portfolio, today=futuro) is True


def test_the_comparison_is_against_the_class_not_in_absolute_terms(db, universo):
    """Que los A subieran un 12% no dice nada si su clase subió un 15%.

    La foto se toma con fecha pasada para que haya precios posteriores que
    medir, que es lo que el caso real tendrá dentro de seis meses.
    """
    portfolio, assets = universo
    hace_un_trimestre = dt.date.today() - dt.timedelta(days=200)

    scored = opp._scored_universe(db, portfolio, assets)
    # Se congela con la fecha antigua y el PRECIO de esa fecha, que es lo que
    # habría guardado una captura hecha entonces.
    serie = {
        a.symbol: db.scalars(
            select(PriceHistory.close).where(
                PriceHistory.asset_id == a.id,
                PriceHistory.date == hace_un_trimestre,
            )
        ).first()
        for a in assets
    }
    for fila in scored.rows:
        fila.current_price = serie[fila.symbol]
    scorecard_service.capture(
        db, portfolio, rows=list(scored.rows), today=hace_un_trimestre
    )
    db.commit()

    tarjeta = scorecard_service.evaluate(
        db, portfolio, captured_on=hace_un_trimestre
    )

    assert tarjeta.horizon_days == 200
    assert tarjeta.outcomes, "Con 200 días de precios posteriores hay algo que medir"
    assert all(r.asset_class == "accion" for r in tarjeta.outcomes)
    # El exceso es contra la mediana de SU clase, así que las notas de una
    # misma clase no pueden estar todas por encima ni todas por debajo.
    excesos = [r.excess_vs_class_pct for r in tarjeta.outcomes]
    assert min(excesos) <= 0 <= max(excesos)


def test_nothing_here_is_ever_conclusive(db, universo):
    """Está en la API para que nadie tenga que preguntarlo."""
    portfolio, _ = universo
    tarjeta = scorecard_service.evaluate(
        db, portfolio, captured_on=dt.date.today()
    )
    assert tarjeta.is_conclusive is False


def test_a_short_horizon_is_declared_as_noise(db, universo):
    portfolio, assets = universo
    scored = opp._scored_universe(db, portfolio, assets)
    scorecard_service.capture(db, portfolio, rows=list(scored.rows))
    db.commit()

    tarjeta = scorecard_service.evaluate(db, portfolio, captured_on=dt.date.today())
    assert any("ruido de mercado" in aviso for aviso in tarjeta.warnings)


# ----------------------------------------------------------------------
# Una foto mala es peor que ninguna
# ----------------------------------------------------------------------


def test_no_snapshot_is_taken_when_the_sync_left_half_the_universe_out(db, monkeypatch):
    """A posteriori una foto mala es indistinguible de una buena.

    En las últimas ocho sincronizaciones reales de esta instalación hubo una
    con 420 símbolos caídos de 494 y otra con 469. Una foto de ese día
    registra scores calculados con datos viejos para el 85% del universo, y
    meses después el scorecard la compararía creyendo que era el ranking de
    ese día. Por eso se descarta ANTES.
    """
    from app.core.config import settings
    from app.services import ingestion
    from tests.fakes import FakeProvider

    capturas = []
    monkeypatch.setattr(
        scorecard_service, "capture", lambda *a, **k: capturas.append(1) or 0
    )
    monkeypatch.setattr(settings, "enable_reference_rates", False)

    # `FakeProvider` vacío: todos los símbolos fallan.
    hoy = dt.date.today()
    for simbolo in ("A", "B", "C", "D", "E", "F"):
        item = Asset(symbol=simbolo, currency="USD", sector="Technology", is_universe=True)
        db.add(item)
        db.flush()
        db.add(PriceHistory(asset_id=item.id, date=hoy, close=100.0, adj_close=100.0))
    db.commit()

    corrida = ingestion.run_sync(db, FakeProvider(), force=True)

    assert corrida.symbols_failed > 0
    assert capturas == [], "No puede congelarse una foto sobre datos a medias"


def test_a_sync_with_a_couple_of_failures_still_takes_the_snapshot(db, monkeypatch):
    """El umbral no puede ser «cero fallos»: nunca habría foto.

    Una corrida real con 6 caídos de 494 actualizó los 494 fundamentales. Es
    un día perfectamente bueno, y descartarlo dejaría el registro vacío para
    siempre.
    """
    from app.core.config import settings

    assert 0 < settings.scorecard_max_failed_share < 0.5, (
        "Ni cero -nunca habría foto- ni tan alto que deje pasar un día roto"
    )
    assert settings.scorecard_max_failed_share > 6 / 494
    assert settings.scorecard_max_failed_share < 420 / 494
