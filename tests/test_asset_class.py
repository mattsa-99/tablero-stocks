"""Clase de activo derivada de datos, y lo que se sigue de ella.

Los tres fallos que estas pruebas fijan comparten lo peor: producen un número
plausible sin lanzar nada. Un fondo de bonos con la mejor valoración del
universo, 136 activos clasificados como «Diversificado» por suposición, y un
futuro del S&P con calificación A en el puesto 82.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.models import (
    Asset,
    AssetQuote,
    AssetType,
    FundamentalSnapshot,
    Portfolio,
    PriceHistory,
    Transaction,
    TransactionType,
)
from app.repositories import portfolio as portfolio_repo
from app.services import opportunities as opp
from app.services.asset_class import AssetClass, classify, periods_per_year


def make(symbol, **kwargs) -> Asset:
    kwargs.setdefault("asset_type", AssetType.STOCK)
    kwargs.setdefault("currency", "USD")
    return Asset(symbol=symbol, **kwargs)


# ----------------------------------------------------------------------
# La clase sale del proveedor, no de una lista
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("symbol", "kwargs", "esperada"),
    [
        ("SJNK", {"asset_type": AssetType.ETF, "fund_category": "High Yield Bond"},
         AssetClass.RENTA_FIJA),
        ("TLT", {"asset_type": AssetType.ETF, "fund_category": "Long Government"},
         AssetClass.RENTA_FIJA),
        ("GLD", {"asset_type": AssetType.ETF, "fund_category": "Commodities Focused"},
         AssetClass.MATERIAS_PRIMAS),
        ("IBIT", {"asset_type": AssetType.ETF, "fund_category": "Digital Assets"},
         AssetClass.CRIPTO),
        ("VT", {"asset_type": AssetType.ETF, "fund_category": "Global Large-Stock Blend"},
         AssetClass.FONDO_ACCIONES),
        # Mineras de oro: son EMPRESAS, y su P/E es real.
        ("GDX", {"asset_type": AssetType.ETF, "fund_category": "Equity Precious Metals"},
         AssetClass.FONDO_ACCIONES),
        ("BTC-USD", {"asset_type": AssetType.CRYPTO}, AssetClass.CRIPTO),
        ("AAPL", {"sector": "Technology"}, AssetClass.ACCION),
    ],
)
def test_the_class_comes_from_the_provider_category(symbol, kwargs, esperada):
    resultado = classify(make(symbol, **kwargs))
    assert resultado.asset_class is esperada
    assert resultado.is_assumed is False


def test_the_category_is_matched_whole_never_as_a_substring():
    """«Communications» contiene «muni», de «Muni National Interm».

    Al desarrollar esto, una comparación por subcadena dejó a XLC clasificado
    como renta fija. No lanza nada: solo deja a un ETF sectorial sin
    valoración para siempre.
    """
    xlc = make("XLC", asset_type=AssetType.ETF, fund_category="Communications")
    assert classify(xlc).asset_class is AssetClass.FONDO_ACCIONES

    muni = make("MUB", asset_type=AssetType.ETF, fund_category="Muni National Interm")
    assert classify(muni).asset_class is AssetClass.RENTA_FIJA


@pytest.mark.parametrize("symbol", ["ES=F", "GC=F", "CL=F", "CT=F", "ZB=F"])
def test_only_the_suffix_identifies_a_future(symbol):
    """Ni `asset_type` ni `quoteType` los cazan; el sufijo sí.

    Medido en la base real: de los 21 símbolos `=F` del universo, 20 traen
    `quoteType = FUTURE` y `CT=F` llega como `ALTSYMBOL`; y de esos 20,
    `GC=F` y `CL=F` están GUARDADOS COMO STOCK.
    """
    assert classify(make(symbol)).asset_class is AssetClass.DERIVADO
    assert classify(make(symbol, asset_type=AssetType.OTHER)).asset_class is (
        AssetClass.DERIVADO
    )


def test_a_trust_that_only_holds_gold_is_not_a_company():
    """PHYS cotiza como acción y Yahoo le imputa un beneficio por acción.

    Ese «beneficio» de 5,51 es la revalorización del oro, así que cuanto más
    sube el metal más barato parece el fondo: le daba una valoración de 98,8
    sobre 100. Sin sector ni industria no se puede afirmar que sea una empresa,
    y decirlo es mejor que suponerlo.
    """
    phys = classify(make("PHYS"))
    assert phys.asset_class is AssetClass.DESCONOCIDO
    assert phys.is_assumed is True
    assert phys.is_earnings_backed is False


def test_only_shares_and_funds_of_shares_admit_a_multiple():
    assert classify(make("AAPL", sector="Technology")).is_earnings_backed is True
    tlt = make("TLT", asset_type=AssetType.ETF, fund_category="Long Government")
    assert classify(tlt).is_earnings_backed is False


def test_an_assumed_class_does_not_earn_the_right_to_a_multiple():
    """«Lo más probable» no es base para conservar una valoración.

    Yahoo no categoriza los fondos de la BVC. `GXTESCOL.CL` es un ETF de TES
    -deuda pública colombiana- y sin esta regla se le supondría de acciones y
    conservaría un «P/E» que no significa nada. Es el fallo de SJNK por otra
    puerta: la categoría lo tapa para los 182 fondos de hoy, no para los que
    se añadan mañana.

    Se sigue rankeando entre fondos -no desaparece- pero sin valoración.
    """
    sin_categoria = classify(make("GXTESCOL.CL", asset_type=AssetType.ETF))

    assert sin_categoria.asset_class is AssetClass.FONDO_ACCIONES, (
        "Sigue entrando al ranking: desaparecer sería peor que no valorarlo"
    )
    assert sin_categoria.is_assumed is True
    assert sin_categoria.is_earnings_backed is False

    # Y uno categorizado SÍ lo conserva: la regla no puede vaciar a todos.
    con_categoria = make("IVV", asset_type=AssetType.ETF, fund_category="Large Blend")
    assert classify(con_categoria).is_earnings_backed is True


def test_an_uncategorised_fund_loses_its_multiples_at_the_border():
    """La comprobación de extremo a extremo: el filtro de datos lo respeta."""
    from app.services import data_quality

    fondo = make("GXTESCOL.CL", asset_type=AssetType.ETF)
    resultado = data_quality.sanitise_multiples(
        {
            "trailing_pe": 8.4, "forward_pe": None, "price_to_book": 1.1,
            "price_to_sales": None, "ev_to_ebitda": None, "eps_trailing": 7000.0,
        },
        quote_currency="COP",
        financial_currency="COP",
        asset_class=classify(fondo).asset_class,
        is_assumed=classify(fondo).is_assumed,
    )
    assert resultado.values["trailing_pe"] is None
    assert resultado.values["price_to_book"] is None
    assert resultado.values["eps_trailing"] is None


def test_crypto_trades_every_day_of_the_year():
    assert periods_per_year(make("BTC-USD", asset_type=AssetType.CRYPTO)) == 365
    assert periods_per_year(make("AAPL", sector="Technology")) == 252


# ----------------------------------------------------------------------
# Lo que no se puede comprar no se rankea
# ----------------------------------------------------------------------


def test_futures_leave_the_actionable_ranking(db):
    """Un futuro del E-mini S&P no es una respuesta para esta cartera.

    Equivale a 50 veces el índice, exige garantía, vence cada trimestre y
    duplica a un ETF que sí se puede comprar. Medido antes del cambio: de los
    21 futuros del universo, 7 salían con calificación A -`ES=F` en el puesto
    82- porque solo tienen 2 señales que suspender.

    Se saca ANTES de puntuar, a diferencia de los filtros de vista: dejarlo
    dentro contamina los percentiles de los demás con un candidato imposible.
    """
    hoy = dt.date.today()
    catalogo = [
        ("ES=F", None), ("AAPL", "Technology"), ("KO", "Consumer Defensive"),
        ("JNJ", "Healthcare"), ("XOM", "Energy"), ("PG", "Consumer Defensive"),
        ("MSFT", "Technology"),
    ]
    for simbolo, sector in catalogo:
        asset = Asset(symbol=simbolo, sector=sector, currency="USD", is_universe=True)
        db.add(asset)
        db.flush()
        precio = 100.0
        for dia in range(300):
            precio *= 1.001
            db.add(PriceHistory(
                asset_id=asset.id, date=hoy - dt.timedelta(days=300 - dia),
                close=precio, adj_close=precio,
            ))
        db.add(FundamentalSnapshot(
            asset_id=asset.id, as_of=hoy, trailing_pe=25.0,
            fetched_at=dt.datetime.now(dt.UTC),
        ))
        db.add(AssetQuote(
            asset_id=asset.id, price=precio, currency="USD",
            fetched_at=dt.datetime.now(dt.UTC), is_stale=False, source="test",
        ))

    portfolio = Portfolio(name="Prueba", base_currency="USD")
    db.add(portfolio)
    db.flush()
    ko = db.query(Asset).filter(Asset.symbol == "KO").one()
    db.add(Transaction(
        portfolio_id=portfolio.id, asset_id=ko.id, type=TransactionType.BUY,
        executed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
        quantity=Decimal("1"), price=Decimal("100"), fees=Decimal("0"),
        currency="USD", fx_rate_to_base=Decimal("1"),
    ))
    db.commit()

    resultado = opp.compute_opportunities(
        db, portfolio_repo.get_portfolio(db, portfolio.id),
        assets=db.query(Asset).all(), limit=50,
    )

    visibles = {fila.symbol for fila in resultado.opportunities}
    assert "ES=F" not in visibles
    assert "futuro" in resultado.excluded["ES=F"].lower()
    assert resultado.universe_size == len(catalogo) - 1, (
        "El futuro no cuenta como candidato evaluado"
    )


# ----------------------------------------------------------------------
# Se compara dentro de una clase, se asigna entre clases
# ----------------------------------------------------------------------


@pytest.fixture
def universo_mixto(db):
    """Acciones, fondos de acciones y fondos de bonos, con datos suficientes."""
    hoy = dt.date.today()
    spec = [
        *[(f"ACC{i}", AssetClass.ACCION, None, "Technology", 12.0 + i) for i in range(6)],
        *[(f"FON{i}", AssetClass.FONDO_ACCIONES, "Large Blend", None, 24.0 + i)
          for i in range(6)],
        *[(f"BON{i}", AssetClass.RENTA_FIJA, "Corporate Bond", None, None)
          for i in range(6)],
    ]
    creados = []
    for simbolo, _clase, categoria, sector, pe in spec:
        tipo = AssetType.STOCK if sector else AssetType.ETF
        item = Asset(
            symbol=simbolo, currency="USD", asset_type=tipo,
            sector=sector, fund_category=categoria, is_universe=True,
        )
        db.add(item)
        db.flush()
        precio = 100.0
        for dia in range(300):
            precio *= 1.002
            db.add(PriceHistory(
                asset_id=item.id, date=hoy - dt.timedelta(days=300 - dia),
                close=precio, adj_close=precio,
            ))
        db.add(FundamentalSnapshot(
            asset_id=item.id, as_of=hoy, trailing_pe=pe,
            fetched_at=dt.datetime.now(dt.UTC),
        ))
        creados.append(item)
    portfolio = Portfolio(name="Mixto", base_currency="USD")
    db.add(portfolio)
    db.commit()
    return portfolio, creados


def test_the_rank_is_within_the_class(db, universo_mixto):
    """Un score percentil por clase no admite un puesto global.

    Ordenar una acción frente a un bono no es una decisión: entre clases lo
    que se decide es el PESO, y eso lo decide el usuario, no un ranking.
    """
    portfolio, assets = universo_mixto
    respuesta = opp.compute_opportunities(db, portfolio, assets=assets, limit=50)

    por_clase = {}
    for fila in respuesta.opportunities:
        por_clase.setdefault(fila.asset_class, []).append(fila)

    assert set(por_clase) == {"accion", "fondo_acciones", "renta_fija"}
    for clase, filas in por_clase.items():
        puestos = sorted(f.rank for f in filas)
        assert puestos == list(range(1, len(filas) + 1)), clase
        assert all(f.class_size == len(filas) for f in filas), clase


def test_a_fund_pe_is_not_ranked_against_a_company_pe(db, universo_mixto):
    """Los fondos tienen P/E 24-29 y las acciones 12-17.

    Con un solo pool, los seis fondos ocuparían las seis peores posiciones de
    valoración por ser fondos, no por ser caros. Dentro de su clase, el más
    barato de cada grupo vuelve a estar arriba.
    """
    portfolio, assets = universo_mixto
    respuesta = opp.compute_opportunities(db, portfolio, assets=assets, limit=50)
    por_simbolo = {f.symbol: f for f in respuesta.opportunities}

    assert por_simbolo["ACC0"].value.score == pytest.approx(
        por_simbolo["FON0"].value.score
    ), "El más barato de cada clase ocupa el mismo percentil"
    assert por_simbolo["FON0"].value_basis == "class"
    assert por_simbolo["FON0"].value_reference == "Fondos de acciones"


def test_a_class_too_small_to_rank_is_excluded_with_a_reason(db, universo_mixto):
    """Hazen sobre n=2 da 25 y 75 pase lo que pase con los valores.

    Un «primer puesto de su clase» así no dice nada, y con el orden por
    calificación acabaría arriba. Se excluye con el motivo a la vista, igual
    que un candidato al que le faltan factores.
    """
    portfolio, assets = universo_mixto
    hoy = dt.date.today()
    suelto = Asset(
        symbol="ORO", currency="USD", asset_type=AssetType.ETF,
        fund_category="Commodities Focused", is_universe=True,
    )
    db.add(suelto)
    db.flush()
    precio = 100.0
    for dia in range(300):
        precio *= 1.003
        db.add(PriceHistory(
            asset_id=suelto.id, date=hoy - dt.timedelta(days=300 - dia),
            close=precio, adj_close=precio,
        ))
    db.commit()

    respuesta = opp.compute_opportunities(
        db, portfolio, assets=[*assets, suelto], limit=50
    )
    assert "ORO" not in {f.symbol for f in respuesta.opportunities}
    assert "ORO" in respuesta.excluded
    assert "materias primas" in respuesta.excluded["ORO"].lower()


def test_the_grade_decides_the_order_before_the_score(db, universo_mixto):
    """Un score alto con mala nota no puede encabezar la lista.

    Ordenar solo por score ponía a ETB.CL -calificación «Mala»- en el puesto 2
    de 490, encima de cien empresas mejor calificadas. El score dice quién
    encabeza; la calificación, si vale la pena mirarlo.
    """
    portfolio, assets = universo_mixto
    respuesta = opp.compute_opportunities(db, portfolio, assets=assets, limit=50)

    orden = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "SIN_CALIFICAR": 5}
    clave = [
        (orden[f.assessment.grade], -f.score) for f in respuesta.opportunities
    ]
    assert clave == sorted(clave)


def test_the_frontend_knows_every_asset_class():
    """Añadir una clase solo en Python la dejaría sin chip, en silencio.

    El JS mantiene su propia copia de la tabla -no hay build que la genere-,
    así que la única defensa contra la divergencia es comprobarlo aquí. Es la
    misma guarda que ya existe para las regiones, y nació del mismo fallo.
    """
    import re
    from pathlib import Path

    source = Path("app/static/js/opportunities.js").read_text(encoding="utf-8")

    etiquetas = re.search(r"const CLASS_LABEL = \{(.*?)\};", source, re.S)
    assert etiquetas, "No se encontró CLASS_LABEL en opportunities.js"
    en_js = set(re.findall(r"^\s*(\w+):", etiquetas.group(1), re.M))
    assert en_js == {c.value for c in AssetClass}, (
        f"Solo en JS: {en_js - {c.value for c in AssetClass}}; "
        f"solo en Python: {{c.value for c in AssetClass}} - en_js"
    )

    # El ORDEN solo lleva las clases que llegan al ranking: `derivado` sale
    # por no ser accionable y `desconocido` por no tener pares suficientes.
    orden = re.search(r"const CLASS_ORDER = \[(.*?)\];", source, re.S)
    assert orden
    con_chip = set(re.findall(r'"(\w+)"', orden.group(1)))
    assert con_chip <= en_js
    assert AssetClass.DERIVADO.value not in con_chip
