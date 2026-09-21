"""Relleno del histórico de tipos de cambio.

`fx_rates` acumulaba un tipo por día, pero solo desde que la aplicación empezó
a correr. Sin histórico, una cartera con posiciones en otra divisa no puede
dibujar su curva de valor ni compararse con un índice más allá de ese mes.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.models import FxRateDaily
from app.services.market_data import MarketDataService
from tests.fakes import FakeProvider

PAIR = ("USD", "COP")


def serie(desde: dt.date, dias: int, inicio: float = 3000.0) -> list[tuple[dt.date, float]]:
    return [(desde + dt.timedelta(days=i), inicio + i) for i in range(dias)]


@pytest.fixture
def hoy() -> dt.date:
    return dt.date.today()


def build(fx_history) -> FakeProvider:
    return FakeProvider(fx_history={PAIR: fx_history})


def stored(db, pair=PAIR) -> list[FxRateDaily]:
    return (
        db.query(FxRateDaily)
        .filter(FxRateDaily.base_currency == pair[0], FxRateDaily.quote_currency == pair[1])
        .order_by(FxRateDaily.date)
        .all()
    )


def test_an_empty_table_gets_the_whole_window(db, hoy):
    provider = build(serie(hoy - dt.timedelta(days=10), 10))
    report = MarketDataService(db, provider).refresh_fx_history([PAIR], days=30)

    filas = stored(db)
    assert report.fx_updated == len(filas) > 0
    assert all(f.source == "yfinance:close" for f in filas)


def test_the_current_day_is_never_written_by_the_history(db, hoy):
    """Hoy no ha cerrado: su "cierre" es el último precio, y congelarlo mentiría.

    El tipo del día en curso lo mantiene `refresh_fx` con la cotización viva.
    """
    provider = build(serie(hoy - dt.timedelta(days=3), 4))  # incluye hoy
    MarketDataService(db, provider).refresh_fx_history([PAIR], days=30)

    assert all(f.date < hoy for f in stored(db))


def test_a_gap_before_the_stored_range_is_filled(db, hoy):
    """EL fallo que tuvo la primera versión.

    Con solo "el día siguiente al último", el relleno únicamente sabe avanzar:
    en una base que ya tenía tres semanas pedía desde el día siguiente al
    último y traía 2 filas, dejando intactos los años de hueco ANTERIORES.
    """
    reciente = hoy - dt.timedelta(days=3)
    db.add(FxRateDaily(
        base_currency="USD", quote_currency="COP", date=reciente,
        rate=Decimal("3500"), source="yfinance", fetched_at=dt.datetime.now(dt.UTC),
    ))
    db.commit()

    provider = build(serie(hoy - dt.timedelta(days=30), 30))
    MarketDataService(db, provider).refresh_fx_history([PAIR], days=60)

    filas = stored(db)
    assert len(filas) > 5
    assert min(f.date for f in filas) < reciente, "no se rellenó el hueco anterior"


def test_a_close_overwrites_an_intraday_snapshot(db, hoy):
    """Para un día ya cerrado el canónico es el CIERRE, no lo que había a las 2pm.

    Medido sobre USD/COP real, las filas escritas por el refresco de jornada se
    desviaban del cierre entre 0,1% y 0,9%. Por eso el histórico pisa en vez de
    ignorar el conflicto.
    """
    ayer = hoy - dt.timedelta(days=1)
    db.add(FxRateDaily(
        base_currency="USD", quote_currency="COP", date=ayer,
        rate=Decimal("9999"), source="yfinance", fetched_at=dt.datetime.now(dt.UTC),
    ))
    db.commit()

    provider = build([(ayer, 3200.0)])
    MarketDataService(db, provider).refresh_fx_history([PAIR], days=30)

    fila = next(f for f in stored(db) if f.date == ayer)
    assert fila.rate == Decimal("3200.0")
    assert fila.source == "yfinance:close"


def test_being_up_to_date_asks_for_nothing(db, hoy):
    """Un histórico completo no debe volver a descargarse cada noche."""
    provider = build(serie(hoy - dt.timedelta(days=40), 40))
    service = MarketDataService(db, provider)
    service.refresh_fx_history([PAIR], days=30)

    llamadas = len([c for c in provider.calls if c[0] == "fetch_fx_history"])
    service.refresh_fx_history([PAIR], days=30)
    assert len([c for c in provider.calls if c[0] == "fetch_fx_history"]) == llamadas


def test_the_same_currency_is_not_a_pair(db):
    report = MarketDataService(db, build([])).refresh_fx_history([("USD", "USD")])
    assert report.fx_updated == 0
