"""Curva de valor, comparación con el índice y XIRR.

Todo se deriva del ledger y de las series guardadas: no hay ninguna tabla de
valores históricos que pudiera desincronizarse del histórico de transacciones.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.models import (
    Asset,
    AssetQuote,
    AssetType,
    FxRateDaily,
    Portfolio,
    PriceHistory,
    Transaction,
    TransactionType,
)
from app.services import performance
from app.services import portfolio as portfolio_service

HOY = dt.date.today()


def crear_activo(db, symbol, currency="USD", precios=None, desde=None, sector="Technology"):
    activo = Asset(symbol=symbol, name=symbol, currency=currency,
                   asset_type=AssetType.STOCK, sector=sector)
    db.add(activo)
    db.flush()
    desde = desde or HOY - dt.timedelta(days=60)
    for i, precio in enumerate(precios or []):
        db.add(PriceHistory(asset_id=activo.id, date=desde + dt.timedelta(days=i),
                            close=precio, adj_close=precio))
    return activo


def comprar(db, portfolio, activo, cuando, qty="10", precio="100", fx="1"):
    db.add(Transaction(
        portfolio_id=portfolio.id, asset_id=activo.id, type=TransactionType.BUY,
        executed_at=dt.datetime.combine(cuando, dt.time(15, 0), tzinfo=dt.UTC),
        quantity=Decimal(qty), price=Decimal(precio), fees=Decimal("0"),
        currency=activo.currency, fx_rate_to_base=Decimal(fx),
    ))


@pytest.fixture
def cartera_usd(db):
    p = Portfolio(name="USD", base_currency="USD")
    db.add(p)
    db.flush()
    return p


# ----------------------------------------------------------------------
# Lo básico
# ----------------------------------------------------------------------


def test_an_empty_portfolio_says_so_instead_of_drawing_zero(db, cartera_usd):
    """Una línea plana en cero parecería una cartera que lo perdió todo."""
    serie = performance.compute_series(db, cartera_usd)
    assert serie.points == []
    assert any("no tiene transacciones" in w for w in serie.warnings)


def test_the_curve_follows_the_price(db, cartera_usd):
    inicio = HOY - dt.timedelta(days=20)
    activo = crear_activo(db, "AAA", precios=[100.0 + i for i in range(21)], desde=inicio)
    comprar(db, cartera_usd, activo, inicio, qty="10", precio="100")
    db.commit()

    serie = performance.compute_series(db, cartera_usd, days=60)
    assert len(serie.points) >= 20
    assert serie.points[0].market_value == Decimal("1000")
    # El precio subió 1 por día durante 20 días: 10 títulos x 120.
    assert serie.points[-1].market_value == Decimal("1200")


def test_the_last_point_agrees_with_the_dashboard(db, cartera_usd):
    """LA invariante de coherencia.

    El último punto de la curva se dibuja justo debajo del KPI de valor de
    mercado. Si usaran precios distintos -por ejemplo `adj_close` aquí y el
    precio real allí- la pantalla se contradiría a sí misma, y es el tipo de
    discrepancia que hace desconfiar de todo lo demás.
    """
    inicio = HOY - dt.timedelta(days=10)
    activo = crear_activo(db, "BBB", precios=[100.0] * 11, desde=inicio)
    comprar(db, cartera_usd, activo, inicio, qty="5", precio="100")
    db.add(AssetQuote(asset_id=activo.id, price=100.0, currency="USD",
                      fetched_at=dt.datetime.now(dt.UTC), is_stale=False, source="test"))
    db.commit()

    curva = performance.compute_series(db, cartera_usd, days=30)
    resumen = portfolio_service.get_summary(db, cartera_usd)
    assert curva.points[-1].market_value == resumen.market_value


# ----------------------------------------------------------------------
# Huecos de datos
# ----------------------------------------------------------------------


def test_a_holiday_carries_the_last_price_forward(db, cartera_usd):
    """La BVC y la NYSE no cierran los mismos días.

    Sin arrastrar el último valor conocido, cada festivo de un mercado abriría
    un hueco en la curva del otro y la cartera aparecería desplomada ese día.
    """
    inicio = HOY - dt.timedelta(days=10)
    con_hueco = crear_activo(db, "CCC", precios=[100.0] * 11, desde=inicio)
    # Otro activo cotiza un día en que el primero no: fuerza el relleno.
    otro = crear_activo(db, "DDD", precios=[50.0] * 11, desde=inicio)
    db.query(PriceHistory).filter(
        PriceHistory.asset_id == con_hueco.id,
        PriceHistory.date == inicio + dt.timedelta(days=5),
    ).delete()
    comprar(db, cartera_usd, con_hueco, inicio, qty="1", precio="100")
    comprar(db, cartera_usd, otro, inicio, qty="1", precio="50")
    db.commit()

    serie = performance.compute_series(db, cartera_usd, days=30)
    dia_festivo = [p for p in serie.points if p.date == inicio + dt.timedelta(days=5)]
    assert dia_festivo, "el día se perdió en vez de rellenarse"
    assert dia_festivo[0].market_value == Decimal("150")


def test_a_day_without_any_price_is_not_drawn_as_zero(db, cartera_usd):
    """Es la misma regla que en el resto del sistema: ausente es None, nunca 0.

    Dibujar un cero mostraría una caída del 100% que no ocurrió.
    """
    inicio = HOY - dt.timedelta(days=20)
    # Serie que solo empieza a los 10 días de la compra.
    activo = crear_activo(db, "EEE", precios=[100.0] * 10,
                          desde=inicio + dt.timedelta(days=10))
    comprar(db, cartera_usd, activo, inicio, qty="1", precio="100")
    db.commit()

    serie = performance.compute_series(db, cartera_usd, days=60)
    assert all(p.market_value > Decimal("0") for p in serie.points)
    assert min(p.date for p in serie.points) >= inicio + dt.timedelta(days=10)


# ----------------------------------------------------------------------
# Divisa
# ----------------------------------------------------------------------


def test_a_cop_asset_is_converted_with_the_rate_of_that_day(db):
    """El tipo de cambio de CADA día, no el de hoy aplicado hacia atrás.

    Usar el tipo actual sobre toda la serie borraría justamente el efecto que
    la curva debe mostrar.
    """
    cartera = Portfolio(name="USD", base_currency="USD")
    db.add(cartera)
    db.flush()

    inicio = HOY - dt.timedelta(days=5)
    activo = crear_activo(db, "ECO.CL", currency="COP", precios=[4000.0] * 6, desde=inicio)
    comprar(db, cartera, activo, inicio, qty="1", precio="4000", fx="0.00025")
    for i in range(6):
        # El peso se revalúa: de 4000 a 2000 COP por dólar.
        db.add(FxRateDaily(
            base_currency="COP", quote_currency="USD", date=inicio + dt.timedelta(days=i),
            rate=Decimal("0.00025") * (1 + Decimal(i) / 5), source="test",
            fetched_at=dt.datetime.now(dt.UTC),
        ))
    db.commit()

    serie = performance.compute_series(db, cartera, days=30)
    # Precio constante en COP, peso que se revalúa -> el valor en USD SUBE.
    assert serie.points[0].market_value == Decimal("1.00000")
    assert serie.points[-1].market_value == Decimal("2.00000")


# ----------------------------------------------------------------------
# XIRR
# ----------------------------------------------------------------------


def test_a_short_history_is_not_annualized(db, cartera_usd):
    """Anualizar 18 días multiplica por 20.

    La cartera de prueba real daba un 129% anual que no significaba nada salvo
    "subió un 4% en tres semanas".
    """
    inicio = HOY - dt.timedelta(days=15)
    activo = crear_activo(db, "FFF", precios=[100.0 + i for i in range(16)], desde=inicio)
    comprar(db, cartera_usd, activo, inicio, qty="1", precio="100")
    db.commit()

    serie = performance.compute_series(db, cartera_usd, days=30)
    assert serie.xirr is None
    assert any("no se anualiza" in w for w in serie.warnings)


def test_doubling_your_money_in_a_year_is_about_one_hundred_percent():
    """Comprobación directa de la matemática, sin base de datos."""
    hace_un_ano = HOY - dt.timedelta(days=365)
    tasa = performance.xirr([(hace_un_ano, Decimal("1000"))], Decimal("2000"), HOY)
    assert tasa is not None
    assert 0.98 < tasa < 1.02


def test_money_that_never_went_in_has_no_rate_of_return():
    """Sin cambio de signo no hay raíz. Inventar una tasa sería inventar un dato."""
    assert performance.xirr([], Decimal("100"), HOY) is None
    assert performance.xirr([(HOY, Decimal("100"))], Decimal("0"), HOY) is None


# ----------------------------------------------------------------------
# Índice de referencia
# ----------------------------------------------------------------------


def test_the_benchmark_receives_the_same_money_on_the_same_day(db, cartera_usd):
    """Es lo que hace la comparación honesta.

    La pregunta no es "cuánto subió SPY" sino "cuánto tendría yo si ese mismo
    dinero, puesto ese mismo día, hubiera ido a SPY".
    """
    inicio = HOY - dt.timedelta(days=20)
    activo = crear_activo(db, "GGG", precios=[100.0] * 21, desde=inicio)
    crear_activo(db, "SPY", precios=[10.0 + i for i in range(21)], desde=inicio,
                 sector=None)
    comprar(db, cartera_usd, activo, inicio, qty="10", precio="100")
    db.commit()

    serie = performance.compute_series(db, cartera_usd, days=60)
    assert serie.benchmark_symbol == "SPY"
    # 1.000 USD a 10 USD por participación = 100 participaciones.
    assert serie.points[0].benchmark_value == Decimal("1000")
    # SPY sube de 10 a 30: las mismas 100 participaciones valen 3.000.
    assert serie.points[-1].benchmark_value == Decimal("3000")
    # Y la cartera, plana, se queda en 1.000: el índice ganó.
    assert serie.points[-1].market_value == Decimal("1000")


def test_buying_today_says_to_wait_not_to_sync(db, cartera_usd):
    """Dos causas distintas llevan a acciones OPUESTAS, y confundirlas manda al
    usuario a hacer algo inútil.

    Pasó de verdad a través del asesor: con 288 barras por activo desde 2025 y
    la primera compra hecha hoy, el aviso decía «ejecuta una sincronización».
    Sincronizar no habría arreglado nada: lo único que falta es que pase una
    sesión de mercado.
    """
    inicio = HOY - dt.timedelta(days=30)
    # Histórico completo, pero que TERMINA antes de la compra.
    activo = crear_activo(db, "HHH", precios=[100.0] * 25, desde=inicio)
    comprar(db, cartera_usd, activo, HOY, qty="1", precio="100")
    db.commit()

    serie = performance.compute_series(db, cartera_usd, days=60)

    assert serie.points == []
    aviso = " ".join(serie.warnings)
    assert "no hace falta sincronizar" in aviso
    assert HOY.isoformat() in aviso, "dice cuándo compraste"


def test_no_bars_at_all_does_ask_for_a_sync(db, cartera_usd):
    """El otro caso sí se arregla sincronizando, y tiene que decirlo."""
    activo = crear_activo(db, "III", precios=[])  # sin ninguna barra
    comprar(db, cartera_usd, activo, HOY - dt.timedelta(days=5), qty="1", precio="100")
    db.commit()

    serie = performance.compute_series(db, cartera_usd, days=60)

    assert "ejecuta una sincronización" in " ".join(serie.warnings)
