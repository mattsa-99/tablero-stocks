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
    respuesta debe avisar de que nada alcanza «Favorables».
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
    """Con 3 señales la nota se mide sobre 6 puntos, no sobre 8.

    Un ETF sin estados financieros no puede necesitar 5 puntos sobre un máximo
    de 4: sería imposible por construcción y no por ser peor activo. La
    proporción sigue siendo la regla.
    """
    fondo = assess(
        trailing_pe=12.0, market_pe=MARKET_PE,
        sma_trend=0.15, momentum_12_1=0.40, volatility=0.15, max_drawdown=0.10,
    )
    assert fondo.available_signals == 3
    assert fondo.max_points == 6
    assert fondo.grade == Grade.EXCELLENT
    assert fondo.capped_by_coverage is False


def test_two_perfect_signals_do_not_earn_a_top_grade():
    """La proporción premiaba la AUSENCIA de datos, y está medido.

    Sobre los 490 activos del ranking real, un ETF sacaba A el 40,9% de las
    veces y una acción el 6,3%: 6,5 veces más. No por ser mejor, sino por
    tener 2,71 señales de media frente a 3,96. Con 2 señales, dos aciertos dan
    4/4 y por tanto la nota máxima.

    No se baja a «Mala»: se topa en «Normal», que es exactamente lo que
    significa no tener base para afirmar más.
    """
    dos_senales = assess(
        sma_trend=0.15, momentum_12_1=0.40, volatility=0.15, max_drawdown=0.10
    )
    assert dos_senales.available_signals == 2
    assert dos_senales.points == dos_senales.max_points, "Perfecto en lo medido"
    assert dos_senales.grade == Grade.NEUTRAL
    assert dos_senales.capped_by_coverage is True
    assert any("no hay base para una nota alta" in n for n in dos_senales.notes)


def test_the_cap_never_improves_a_bad_grade():
    """Topar es un techo, no una corrección hacia el centro.

    Un activo malo con pocas señales sigue siendo malo: si el tope lo subiera
    a «Normal» estaría escondiendo justo lo que hay que ver.
    """
    malo = assess(
        sma_trend=-0.15, momentum_12_1=-0.40, volatility=0.70, max_drawdown=0.60
    )
    assert malo.available_signals == 2
    assert malo.grade in (Grade.POOR, Grade.BAD)
    assert malo.capped_by_coverage is False


def test_coverage_is_reported_so_the_interface_can_say_it():
    completo = assess(
        trailing_pe=12.0, market_pe=MARKET_PE,
        sma_trend=0.1, momentum_12_1=0.2, volatility=0.2, max_drawdown=0.2,
        roe=0.22, profit_margin=0.18, debt_to_equity=40.0, revenue_growth=0.12,
    )
    assert completo.coverage == 1.0
    assert completo.available_signals == 4


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


def make(symbol, sector=None, asset_type=AssetType.STOCK, fund_category=None):
    return Asset(
        symbol=symbol, sector=sector, asset_type=asset_type,
        currency="USD", fund_category=fund_category,
    )


@pytest.mark.parametrize(
    ("symbol", "category", "expected"),
    [
        # Fondos de acciones amplios: son diversificación en sí mismos.
        ("SPY", "Large Blend", exposure.BROAD_EQUITY),
        ("QQQ", "Large Growth", exposure.BROAD_EQUITY),
        ("VT", "Global Large-Stock Blend", exposure.BROAD_EQUITY),
        ("IWM", "Small Blend", exposure.BROAD_EQUITY),
        # Renta fija: diversifica frente a la renta variable, no dentro de ella.
        ("TLT", "Long Government", exposure.FIXED_INCOME),
        ("AGG", "Intermediate Core Bond", exposure.FIXED_INCOME),
        ("SJNK", "High Yield Bond", exposure.FIXED_INCOME),
        # Materia prima física o por futuros: no hay empresas dentro.
        ("GLD", "Commodities Focused", exposure.COMMODITIES),
        ("GSG", "Commodities Broad Basket", exposure.COMMODITIES),
        # Un ETF de bitcoin al contado no es renta variable amplia.
        ("IBIT", "Digital Assets", exposure.CRYPTO),
        # Sectoriales: se comportan como una acción de su sector.
        ("XLE", "Equity Energy", "Energy"),
        ("XLF", "Financial", "Financial Services"),
        ("XLV", "Health", "Healthcare"),
        # Mineras de oro son ACCIONES, no oro: su P/E es real.
        ("GDX", "Equity Precious Metals", "Basic Materials"),
    ],
)
def test_the_bucket_comes_from_the_provider_category(symbol, category, expected):
    """El cubo sale de un DATO, no de una lista de símbolos escrita a mano.

    Dos fallos corregidos aquí. El original: Yahoo no da sector a un ETF y el
    motor lo leía como carencia de datos, cuando un ETF amplio no tiene sector
    PORQUE abarca muchos.

    El segundo, medido sobre la base real: con listas de símbolos, **136 de
    490 activos (27,8%) caían en «Diversificado» por suposición**, incluidos
    fondos de deuda pública y ETFs de bitcoin. Con la categoría del proveedor
    los supuestos bajan a 1.
    """
    asset = make(symbol, asset_type=AssetType.ETF, fund_category=category)
    assert exposure.exposure_bucket(asset) == expected
    assert exposure.is_assumed_bucket(asset) is False


def test_a_future_is_not_a_commodity():
    """`ES=F` sigue al S&P 500. Llamarlo materia prima era afirmar algo falso.

    La única regla anterior era el sufijo `=F`, así que los seis futuros de
    índices y los dos de bonos del universo acababan en «Materias primas».
    """
    assert exposure.exposure_bucket(make("ES=F")) == exposure.DERIVATIVES
    assert exposure.exposure_bucket(make("GC=F")) == exposure.DERIVATIVES


def test_a_stock_keeps_its_gics_sector():
    assert exposure.exposure_bucket(make("AAPL", sector="Technology")) == "Technology"


def test_an_uncategorised_fund_is_assumed_broad_and_says_so():
    asset = make("XXYY", asset_type=AssetType.ETF)
    assert exposure.exposure_bucket(asset) == exposure.BROAD_EQUITY
    assert exposure.is_assumed_bucket(asset) is True


def test_a_concentrated_fund_with_no_placeable_sector_is_declared():
    """ICLN reparte entre utilities, tecnología e industriales.

    Ni es amplio ni tiene un sector dominante que la categoría revele. Se
    trata como amplio, pero DECLARÁNDOLO: inventarle un sector sería peor.
    """
    asset = make("ICLN", asset_type=AssetType.ETF, fund_category="Miscellaneous Sector")
    assert exposure.exposure_bucket(asset) == exposure.BROAD_EQUITY
    assert exposure.is_assumed_bucket(asset) is True


def test_a_stock_without_sector_stays_unknown():
    """Una acción sin sector SÍ es una carencia de datos, y se declara."""
    assert exposure.exposure_bucket(make("RARO")) == exposure.UNKNOWN


def test_a_broad_etf_is_no_longer_penalised(db, client):
    """La comprobación de extremo a extremo del fallo.

    SPY y una acción cualquiera, ambos AUSENTES de la cartera, deben recibir
    el MISMO crédito de diversificación. Antes SPY sacaba 50 y la acción 100,
    porque un ETF sin sector GICS caía en «desconocido» y se le imputaba el
    neutro: el sistema desaconsejaba justo el instrumento más diversificador.

    La cartera tiene UNA posición a propósito. Con la cartera vacía el factor
    queda sin medir para todos, y entonces este test pasaría aunque el fallo
    volviera: la igualdad se cumpliría por no haber medido nada.
    """
    import datetime as dt
    from decimal import Decimal

    from app.models import (
        AssetQuote,
        FundamentalSnapshot,
        Portfolio,
        PriceHistory,
        Transaction,
        TransactionType,
    )
    from app.repositories import portfolio as portfolio_repo
    from app.services import opportunities as opp

    today = dt.date.today()
    # Cinco de cada clase: por debajo del mínimo, una clase no se puede
    # ordenar por dentro y queda fuera del ranking. Ver
    # `test_a_class_too_small_to_rank_is_excluded_with_a_reason`.
    symbols = [
        ("SPY", None, AssetType.ETF),
        ("QQQ", None, AssetType.ETF),
        ("VTI", None, AssetType.ETF),
        ("IWM", None, AssetType.ETF),
        ("EFA", None, AssetType.ETF),
        ("AAPL", "Technology", AssetType.STOCK),
        ("KO", "Consumer Defensive", AssetType.STOCK),
        ("JNJ", "Healthcare", AssetType.STOCK),
        ("XOM", "Energy", AssetType.STOCK),
        ("PG", "Consumer Defensive", AssetType.STOCK),
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

    portfolio = Portfolio(name="Con una posición", base_currency="USD")
    db.add(portfolio)
    db.flush()

    # Una posición en Healthcare: ni el cubo de SPY («Diversificado») ni el de
    # AAPL («Technology»), así que ambos siguen ausentes y comparables entre sí.
    jnj = db.query(Asset).filter(Asset.symbol == "JNJ").one()
    db.add(AssetQuote(asset_id=jnj.id, price=100.0, currency="USD",
                      fetched_at=dt.datetime.now(dt.UTC), is_stale=False, source="test"))
    db.add(Transaction(
        portfolio_id=portfolio.id, asset_id=jnj.id, type=TransactionType.BUY,
        executed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
        quantity=Decimal("1"), price=Decimal("100"), fees=Decimal("0"),
        currency="USD", fx_rate_to_base=Decimal("1"),
    ))
    db.commit()

    result = opp.compute_opportunities(
        db, portfolio_repo.get_portfolio(db, portfolio.id),
        assets=db.query(Asset).all(), limit=10,
    )
    by_symbol = {r.symbol: r for r in result.opportunities}

    assert by_symbol["SPY"].diversification.score == by_symbol["AAPL"].diversification.score
    assert by_symbol["SPY"].diversification.available is True
    assert by_symbol["SPY"].exposure_bucket == exposure.BROAD_EQUITY
