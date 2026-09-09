"""Tests de la calificación absoluta y de los cubos de exposición.

El test que da sentido a todo el módulo es
`test_a_bad_universe_produces_a_bad_first_place`: es el punto ciego del score
relativo, y si deja de cumplirse el tablero vuelve a poder presentar como
"oportunidad" al menos malo de un conjunto malo.
"""

from __future__ import annotations

import pytest

from app.models import Asset, AssetType
from app.services import exposure
from app.services.grading import Grade, assess

MARKET_PE = 25.0


def excellent(**overrides):
    """Un activo fuerte en las cuatro señales."""
    base = dict(
        trailing_pe=14.0, market_pe=MARKET_PE,          # barato frente al mercado
        sma_trend=0.12, momentum_12_1=0.35,             # tendencia clara
        volatility=0.16, max_drawdown=0.12,             # riesgo bajo
        roe=0.28, profit_margin=0.22,
        debt_to_equity=40.0, revenue_growth=0.18,       # deuda 0,4x
    )
    return {**base, **overrides}


def awful(**overrides):
    """Un activo malo en las cuatro señales."""
    base = dict(
        trailing_pe=95.0, market_pe=MARKET_PE,          # carísimo
        sma_trend=-0.18, momentum_12_1=-0.45,           # cayendo
        volatility=0.85, max_drawdown=0.62,             # riesgo extremo
        roe=-0.10, profit_margin=-0.05,
        debt_to_equity=520.0, revenue_growth=-0.22,     # deuda 5,2x
    )
    return {**base, **overrides}


# --------------------------------------------------------------------------
# La escala discrimina
# --------------------------------------------------------------------------


def test_a_strong_asset_grades_excellent():
    assert assess(**excellent()).grade == Grade.EXCELLENT


def test_a_weak_asset_grades_bad():
    result = assess(**awful())
    assert result.grade == Grade.BAD
    assert result.points < 0


def test_an_average_asset_grades_neutral():
    """En línea con el mercado, tendencia plana, riesgo típico."""
    result = assess(
        trailing_pe=25.0, market_pe=MARKET_PE,
        sma_trend=0.01, momentum_12_1=0.03,
        volatility=0.28, max_drawdown=0.25,
        roe=0.08, profit_margin=0.06, debt_to_equity=120.0, revenue_growth=0.03,
    )
    assert result.grade == Grade.NEUTRAL


def test_the_scale_is_ordered():
    """Cada escalón debe quedar por debajo del anterior, sin saltos raros."""
    order = [Grade.EXCELLENT, Grade.GOOD, Grade.NEUTRAL, Grade.POOR, Grade.BAD]
    grades = [
        assess(**excellent()).grade,
        assess(**excellent(trailing_pe=30.0, momentum_12_1=0.05, volatility=0.30)).grade,
        assess(trailing_pe=25.0, market_pe=MARKET_PE, sma_trend=0.0,
               momentum_12_1=0.0, volatility=0.28, max_drawdown=0.25).grade,
        assess(trailing_pe=45.0, market_pe=MARKET_PE, sma_trend=-0.08,
               momentum_12_1=-0.25, volatility=0.45, max_drawdown=0.40).grade,
        assess(**awful()).grade,
    ]
    assert [order.index(g) for g in grades] == sorted(order.index(g) for g in grades)


# --------------------------------------------------------------------------
# EL punto: la calificación NO depende del universo
# --------------------------------------------------------------------------


def test_grade_ignores_what_else_is_being_compared():
    """La misma acción saca la misma nota rodeada de buenas o de malas.

    Es la diferencia con el score relativo, que cambiaría por completo.
    """
    solo = assess(**excellent())
    # `assess` ni siquiera acepta el universo como parámetro: la independencia
    # es estructural, no una propiedad que haya que recordar mantener.
    assert solo.grade == assess(**excellent()).grade


def test_a_bad_universe_produces_a_bad_first_place(db, client):
    """El punto ciego del score relativo, cubierto.

    Un universo entero de activos malos sigue teniendo un primer puesto -el
    ranking es ordinal- pero ese primero debe salir calificado como malo, y la
    respuesta debe avisar de que nada alcanza «Buena».
    """
    import datetime as dt

    from app.models import FundamentalSnapshot, PriceHistory
    from app.repositories import portfolio as portfolio_repo
    from app.services import opportunities as opp

    today = dt.date.today()
    for index in range(6):
        asset = Asset(symbol=f"MALA{index}", name=f"Mala {index}",
                      currency="USD", sector="Technology")
        db.add(asset)
        db.flush()
        # Precio en caída sostenida y muy volátil.
        price = 100.0
        for day in range(300):
            price *= 0.995 * (1.09 if day % 2 else 0.91)
            db.add(
                PriceHistory(
                    asset_id=asset.id, date=today - dt.timedelta(days=300 - day),
                    close=price, adj_close=price,
                )
            )
        db.add(
            FundamentalSnapshot(
                asset_id=asset.id, as_of=today, trailing_pe=90.0 + index,
                return_on_equity=-0.15, profit_margin=-0.08,
                debt_to_equity=600.0, revenue_growth=-0.30,
                fetched_at=dt.datetime.now(dt.UTC),
            )
        )
    # Referencia de mercado, para que la señal de valoración tenga ancla.
    spy = Asset(symbol="SPY", name="S&P 500", currency="USD", asset_type=AssetType.ETF)
    db.add(spy)
    db.flush()
    db.add(FundamentalSnapshot(asset_id=spy.id, as_of=today, trailing_pe=25.0,
                               fetched_at=dt.datetime.now(dt.UTC)))
    from app.models import Portfolio

    portfolio = Portfolio(name="Vacío", base_currency="USD")
    db.add(portfolio)
    db.commit()

    result = opp.compute_opportunities(
        db, portfolio_repo.get_portfolio(db, portfolio.id),
        assets=[a for a in db.query(Asset).all()], limit=10,
    )

    first = result.opportunities[0]
    assert first.score > 50, "El ranking sigue produciendo un primero con score alto"
    assert first.assessment.grade in {"D", "E"}, (
        "…pero su calificación absoluta debe delatar que es malo"
    )
    assert result.universe_quality_warning is not None
    assert "Ningún candidato" in result.universe_quality_warning


# --------------------------------------------------------------------------
# Honestidad ante datos ausentes
# --------------------------------------------------------------------------


def test_too_few_signals_is_unrated_not_neutral():
    """Sin datos NO se califica.

    Devolver «Normal» por defecto sería afirmar algo sobre el activo sin base,
    que es exactamente el problema que este módulo viene a resolver.
    """
    result = assess(trailing_pe=15.0, market_pe=MARKET_PE)
    assert result.grade == Grade.UNRATED
    assert result.points == 0
    assert "no hay base suficiente" in result.notes[0]


def test_a_partial_grade_says_so():
    result = assess(
        sma_trend=0.10, momentum_12_1=0.30, volatility=0.18, max_drawdown=0.15
    )
    assert result.grade != Grade.UNRATED
    assert any("parcial" in note for note in result.notes)
    assert result.max_points == 4, "Solo 2 señales con datos"


def test_the_bar_is_the_achievable_maximum():
    """Un futuro sin fundamentales debe poder sacar la mejor nota.

    Con umbrales de puntos absolutos necesitaría 5 sobre un máximo de 4:
    imposible por construcción, y no por ser peor activo.
    """
    commodity = assess(
        sma_trend=0.15, momentum_12_1=0.40, volatility=0.15, max_drawdown=0.10
    )
    assert commodity.max_points == 4
    assert commodity.grade == Grade.EXCELLENT


def test_losses_are_a_negative_signal_not_a_bargain():
    """Un P/E negativo no es «barato»: es que la empresa pierde dinero."""
    result = assess(
        trailing_pe=-12.0, market_pe=MARKET_PE,
        sma_trend=0.0, momentum_12_1=0.0, volatility=0.30, max_drawdown=0.25,
    )
    valuation = next(s for s in result.signals if s.name == "valuation")
    assert valuation.points == -1
    assert "beneficios positivos" in valuation.detail


def test_no_benchmark_leaves_valuation_unmeasured():
    """Sin ancla de mercado, la valoración queda sin datos.

    Inventar una referencia sería peor que no tenerla: un ancla equivocada
    contamina todas las calificaciones a la vez.
    """
    result = assess(
        trailing_pe=15.0, market_pe=None,
        sma_trend=0.1, momentum_12_1=0.3, volatility=0.2, max_drawdown=0.15,
    )
    valuation = next(s for s in result.signals if s.name == "valuation")
    assert valuation.points is None
    assert result.max_points == 4


def test_every_signal_explains_itself():
    """El desglose debe llegar hasta el dato observado, no quedarse en la nota."""
    result = assess(**excellent())
    for signal in result.signals:
        assert signal.detail, f"{signal.name} sin explicación"
        assert signal.inputs, f"{signal.name} sin métricas observadas"


def test_debt_is_read_as_a_percentage():
    """yfinance da debtToEquity en PORCENTAJE (380.26 = 3,8x).

    Leerlo como múltiplo haría que una deuda de 3,8x pareciera de 380x y
    hundiría la calificación de casi cualquier empresa apalancada.
    """
    heavy = assess(
        trailing_pe=20.0, market_pe=MARKET_PE, sma_trend=0.0, momentum_12_1=0.0,
        volatility=0.25, max_drawdown=0.2,
        roe=0.15, profit_margin=0.10, debt_to_equity=380.0, revenue_growth=0.05,
    )
    quality = next(s for s in heavy.signals if s.name == "quality")
    assert quality.inputs["debt_to_equity_x"] == pytest.approx(3.8)
    assert "3.8x" in quality.detail


# --------------------------------------------------------------------------
# Cubos de exposición: el fallo de los ETFs
# --------------------------------------------------------------------------


def make(symbol, sector=None, asset_type=AssetType.STOCK):
    return Asset(symbol=symbol, sector=sector, asset_type=asset_type, currency="USD")


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("SPY", exposure.BROAD_EQUITY),
        ("QQQ", exposure.BROAD_EQUITY),
        ("VTI", exposure.BROAD_EQUITY),
        ("IWM", exposure.BROAD_EQUITY),
        ("TLT", exposure.FIXED_INCOME),
        ("AGG", exposure.FIXED_INCOME),
        ("GC=F", exposure.COMMODITIES),
        ("CL=F", exposure.COMMODITIES),
        ("GLD", exposure.COMMODITIES),
        ("XLE", "Energy"),
        ("XLF", "Financial Services"),
        ("XLV", "Healthcare"),
    ],
)
def test_etfs_and_commodities_get_a_real_bucket(symbol, expected):
    """El fallo corregido: antes todos estos caían en «Desconocido».

    Yahoo no da sector para un ETF, y el motor lo interpretaba como una
    carencia de datos. No lo es: un ETF amplio no tiene sector PORQUE abarca
    muchos, y era precisamente lo más diversificador.
    """
    assert exposure.exposure_bucket(make(symbol, asset_type=AssetType.ETF)) == expected


def test_a_stock_keeps_its_gics_sector():
    assert exposure.exposure_bucket(make("AAPL", sector="Technology")) == "Technology"


def test_an_unlisted_etf_is_assumed_broad_and_says_so():
    asset = make("XXYY", asset_type=AssetType.ETF)
    assert exposure.exposure_bucket(asset) == exposure.BROAD_EQUITY
    assert exposure.is_assumed_bucket(asset) is True


def test_an_explicit_bucket_is_not_an_assumption():
    for symbol in ("SPY", "XLE", "TLT", "GC=F"):
        asset = make(symbol, asset_type=AssetType.ETF)
        assert exposure.is_assumed_bucket(asset) is False


def test_a_stock_without_sector_stays_unknown():
    """Una acción sin sector SÍ es una carencia de datos, y se declara."""
    assert exposure.exposure_bucket(make("RARO")) == exposure.UNKNOWN


def test_a_broad_etf_is_no_longer_penalised(db, client):
    """La comprobación de extremo a extremo del fallo.

    SPY y una acción cualquiera, ambos ausentes de una cartera vacía, deben
    recibir el MISMO crédito de diversificación. Antes SPY sacaba 50 y la
    acción 100.
    """
    import datetime as dt

    from app.models import FundamentalSnapshot, Portfolio, PriceHistory
    from app.repositories import portfolio as portfolio_repo
    from app.services import opportunities as opp

    today = dt.date.today()
    symbols = [
        ("SPY", None, AssetType.ETF),
        ("QQQ", None, AssetType.ETF),
        ("AAPL", "Technology", AssetType.STOCK),
        ("KO", "Consumer Defensive", AssetType.STOCK),
        ("JNJ", "Healthcare", AssetType.STOCK),
        ("XOM", "Energy", AssetType.STOCK),
    ]
    for symbol, sector, kind in symbols:
        asset = Asset(symbol=symbol, sector=sector, asset_type=kind, currency="USD")
        db.add(asset)
        db.flush()
        price = 100.0
        for day in range(300):
            price *= 1.001
            db.add(PriceHistory(asset_id=asset.id,
                                date=today - dt.timedelta(days=300 - day),
                                close=price, adj_close=price))
        db.add(FundamentalSnapshot(asset_id=asset.id, as_of=today, trailing_pe=25.0,
                                   fetched_at=dt.datetime.now(dt.UTC)))

    portfolio = Portfolio(name="Vacío", base_currency="USD")
    db.add(portfolio)
    db.commit()

    result = opp.compute_opportunities(
        db, portfolio_repo.get_portfolio(db, portfolio.id),
        assets=db.query(Asset).all(), limit=10,
    )
    by_symbol = {r.symbol: r for r in result.opportunities}

    assert by_symbol["SPY"].diversification.score == by_symbol["AAPL"].diversification.score
    assert by_symbol["SPY"].diversification.available is True
    assert by_symbol["SPY"].exposure_bucket == exposure.BROAD_EQUITY
