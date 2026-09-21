"""Renta fija directa: TES, CDT y FIC cargados a mano.

Lo que estas pruebas fijan es que el tablero NUNCA presente un devengo como si
fuera un precio de mercado, y que las comparaciones se hagan contra lo que
corresponde: la inflación contra la tasa neta, el mercado contra la bruta.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.models import (
    Asset,
    AssetQuote,
    AssetType,
    FixedIncomeTerms,
    RateKind,
    ReferenceRate,
)
from app.services import fixed_income as fi
from app.services import reference_rates as rates_service


def cdt(db, **kwargs) -> tuple[Asset, FixedIncomeTerms]:
    hoy = dt.date.today()
    asset = Asset(
        symbol=kwargs.pop("symbol", "CDT-X"),
        currency=kwargs.pop("currency", "COP"),
        asset_type=AssetType.FIXED_INCOME,
        name="CDT de prueba",
    )
    db.add(asset)
    db.flush()
    terms = FixedIncomeTerms(
        asset_id=asset.id,
        issuer=kwargs.pop("issuer", "Bancolombia"),
        rate_kind=kwargs.pop("rate_kind", RateKind.FIXED),
        annual_rate_pct=Decimal(str(kwargs.pop("rate", "13.02"))),
        issued_on=kwargs.pop("issued_on", hoy - dt.timedelta(days=365)),
        matures_on=kwargs.pop("matures_on", hoy + dt.timedelta(days=365)),
        has_secondary_market=kwargs.pop("has_secondary_market", False),
        withholding_pct=kwargs.pop("withholding_pct", None),
        **kwargs,
    )
    db.add(terms)
    db.commit()
    return asset, terms


def cargar_tasas(db, **valores) -> None:
    hoy = dt.date.today()
    for serie, valor in valores.items():
        db.add(ReferenceRate(
            series=serie, as_of=hoy, value=valor, unit="%",
            source="test", fetched_at=dt.datetime.now(dt.UTC),
        ))
    db.commit()


# ----------------------------------------------------------------------
# Devengo
# ----------------------------------------------------------------------


def test_a_year_of_accrual_is_exactly_the_annual_rate(db):
    """La comprobación aritmética: un año al 13,02% da un factor de 1,1302."""
    _, terms = cdt(db, issued_on=dt.date.today() - dt.timedelta(days=365))
    devengo = fi.accrue(terms, Decimal("13.02"))

    assert devengo.days_elapsed == 365
    assert devengo.factor == pytest.approx(Decimal("1.1302"), abs=Decimal("0.0001"))


def test_the_accrual_never_presents_itself_as_a_market_price(db):
    """Un número presentado como valor de mercado cuando no lo es sería
    exactamente el dato plausible y falso que el resto del sistema filtra."""
    _, terms = cdt(db)
    devengo = fi.accrue(terms, Decimal("13.02"))

    assert devengo.basis == "costo_mas_devengo"
    assert "vencimiento" in devengo.caveat.lower()
    assert "no es lo que alguien pagaría hoy" in devengo.caveat.lower()


def test_accrual_stops_at_maturity(db):
    """Pasado el vencimiento no sigue devengando: ya no le debe intereses a nadie."""
    hoy = dt.date.today()
    _, terms = cdt(
        db,
        issued_on=hoy - dt.timedelta(days=730),
        matures_on=hoy - dt.timedelta(days=365),
    )
    devengo = fi.accrue(terms, Decimal("10"))

    assert devengo.is_matured is True
    assert devengo.days_elapsed == 365, "Solo devengó hasta que venció"
    assert "Venció" in devengo.caveat


# ----------------------------------------------------------------------
# Tasa efectiva según el tipo
# ----------------------------------------------------------------------


def test_an_indexed_rate_is_composed_with_the_published_index(db):
    """IBR + 2,50 no es 2,50: presentarlo así erraría por cinco veces."""
    cargar_tasas(db, ibr_3m=11.59)
    _, terms = cdt(db, rate_kind=RateKind.IBR, rate="2.50")

    tasa, procedencia = fi.effective_annual_rate(terms, rates_service.latest(db))
    assert tasa == Decimal("14.09")
    assert "IBR a 3 meses" in procedencia
    assert "11.59" in procedencia, "La fecha y el valor del índice van a la vista"


def test_without_the_index_the_rate_is_not_invented(db):
    """Sin el índice se devuelve None, no el spread como si fuera la tasa."""
    _, terms = cdt(db, rate_kind=RateKind.IBR, rate="2.50")

    tasa, procedencia = fi.effective_annual_rate(terms, {})
    assert tasa is None
    assert "sin el valor de IBR" in procedencia


def test_a_uvr_rate_is_already_real_and_inflation_is_not_added_twice(db):
    """La UVR ajusta el CAPITAL con la inflación: la tasa pactada ya es real."""
    cargar_tasas(db, inflacion_anual=6.24)
    asset, terms = cdt(db, rate_kind=RateKind.UVR, rate="5.28")

    tasa, procedencia = fi.effective_annual_rate(terms, rates_service.latest(db))
    assert tasa == Decimal("5.28")
    assert "real" in procedencia

    evaluacion = fi.assess(db, asset, terms)
    assert evaluacion.real_rate_pct == pytest.approx(5.28)


# ----------------------------------------------------------------------
# Las comparaciones: cada una contra lo que corresponde
# ----------------------------------------------------------------------


def test_inflation_is_compared_against_the_net_rate(db):
    """Lo que queda de poder adquisitivo es lo que se recibe, no lo que se pacta."""
    cargar_tasas(db, inflacion_anual=6.24, cdt_360d=12.31)
    asset, terms = cdt(db, rate="13.02", withholding_pct=Decimal("7"))

    evaluacion = fi.assess(db, asset, terms)
    assert evaluacion.net_rate_pct == pytest.approx(12.1086)
    # Fisher exacto, no la resta: 12,1086 - 6,24 daría 5,87 y no 5,52.
    assert evaluacion.real_rate_pct == pytest.approx(5.5239, abs=0.001)
    assert evaluacion.beats_inflation is True


def test_the_market_is_compared_gross_against_gross(db):
    """La referencia de Banrep es la tasa pactada, antes de retención.

    Compararla con la NETA diría que el instrumento pierde contra el mercado
    por una retención que los demás también pagan.
    """
    cargar_tasas(db, inflacion_anual=6.24, cdt_360d=12.31)
    asset, terms = cdt(db, rate="13.02", withholding_pct=Decimal("40"))

    evaluacion = fi.assess(db, asset, terms)
    assert evaluacion.net_rate_pct < evaluacion.market_reference_pct
    assert evaluacion.beats_market is True, (
        "13,02% bruto SÍ le gana al 12,31% del mercado, retención aparte"
    )


def test_the_reference_matches_the_remaining_term(db):
    """Un CDT a seis meses no se compara con un TES a diez años."""
    hoy = dt.date.today()
    cargar_tasas(db, cdt_180d=11.18, cdt_360d=12.31, tes_cop_10y=12.66)

    asset, corto = cdt(db, symbol="C1", matures_on=hoy + dt.timedelta(days=100))
    assert fi.assess(db, asset, corto).market_reference_label.startswith("CDT a 180")

    asset2, largo = cdt(db, symbol="C2", matures_on=hoy + dt.timedelta(days=3000))
    assert "10 años" in fi.assess(db, asset2, largo).market_reference_label


def test_a_missing_withholding_is_declared_not_assumed_zero(db):
    """Sin retención declarada, la comparación sale mejor de lo que será."""
    cargar_tasas(db, inflacion_anual=6.24)
    asset, terms = cdt(db, withholding_pct=None)

    evaluacion = fi.assess(db, asset, terms)
    assert evaluacion.net_rate_pct is None
    assert any("tasa BRUTA" in a for a in evaluacion.warnings)


def test_it_is_never_scored_nor_graded(db):
    """Un score es un rango percentil contra pares, y aquí no hay pares."""
    asset, terms = cdt(db)
    evaluacion = fi.assess(db, asset, terms)

    assert not hasattr(evaluacion, "score")
    assert not hasattr(evaluacion, "grade")
    assert len(evaluacion.questions) == 5


def test_the_questions_name_the_issuer_and_the_lock_in(db):
    """En renta fija el emisor ES el riesgo, y la liquidez no sale de ninguna cifra."""
    asset, terms = cdt(db, issuer="Banco Pichincha", has_secondary_market=False)
    preguntas = " ".join(fi.assess(db, asset, terms).questions)

    assert "Banco Pichincha" in preguntas
    assert "NO tiene mercado secundario" in preguntas
    assert terms.matures_on.isoformat() in preguntas


# ----------------------------------------------------------------------
# Encaje con el resto del sistema
# ----------------------------------------------------------------------


def test_the_accrual_is_published_as_a_quote_so_nothing_needs_a_special_case(db):
    """Con la cotización publicada, el valor de la posición y el reparto por
    clase funcionan sin saber que esto no cotiza."""
    asset, _ = cdt(db, rate="13.02")

    escritas = fi.publish_accruals(db)
    assert escritas == 1

    quote = db.get(AssetQuote, asset.id)
    assert quote is not None
    assert quote.source == "devengo", "La procedencia queda declarada en la fila"
    assert quote.price == pytest.approx(1.1302, abs=0.001)


def test_fixed_income_never_enters_the_ingestion_universe(db):
    """No hay nada que pedirle a ningún proveedor: sería un 404 por sincronización."""
    asset, _ = cdt(db)
    assert asset.is_universe is False


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------


def test_registering_an_instrument_creates_the_asset_but_not_the_purchase(client, db_of):
    """La compra se registra como cualquier otra transacción.

    Así el ledger sigue siendo la única fuente de verdad y el replay no
    necesita ni un caso especial: `quantity` = el capital y `price` = 1.
    """
    from app.models import Transaction

    respuesta = client.post("/api/fixed-income", json={
        "symbol": "cdt-bancolombia-2027",
        "issuer": "Bancolombia",
        "currency": "COP",
        "rate_kind": "FIXED",
        "annual_rate_pct": "13.02",
        "issued_on": "2026-09-01",
        "matures_on": "2027-09-01",
        "has_secondary_market": False,
        "withholding_pct": "7",
    })
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["symbol"] == "CDT-BANCOLOMBIA-2027", "El símbolo se normaliza"
    assert cuerpo["accrual"]["basis"] == "costo_mas_devengo"
    assert "no lo que alguien pagaría hoy" in cuerpo["disclaimer"]

    assert db_of.query(Transaction).count() == 0, "Registrar no es comprar"
    asset = db_of.query(Asset).filter(Asset.symbol == "CDT-BANCOLOMBIA-2027").one()
    assert asset.asset_type is AssetType.FIXED_INCOME
    assert asset.is_universe is False


def test_a_duplicate_symbol_is_refused(client):
    payload = {
        "symbol": "CDT-X", "issuer": "Banco", "annual_rate_pct": "12",
        "issued_on": "2026-01-01", "matures_on": "2027-01-01",
    }
    assert client.post("/api/fixed-income", json=payload).status_code == 201
    repetido = client.post("/api/fixed-income", json=payload)
    assert repetido.status_code == 409
    assert "Ya existe" in repetido.json()["detail"]


def test_an_instrument_with_operations_cannot_be_deleted(client, db_of):
    """Borrarlo dejaría el histórico sin cuadrar: el ledger manda."""
    from decimal import Decimal as D

    from app.models import Transaction, TransactionType

    client.post("/api/fixed-income", json={
        "symbol": "CDT-Y", "issuer": "Banco", "annual_rate_pct": "12",
        "issued_on": "2026-01-01", "matures_on": "2027-01-01",
    })
    asset = db_of.query(Asset).filter(Asset.symbol == "CDT-Y").one()
    from app.models import Portfolio

    portfolio = db_of.query(Portfolio).first()
    if portfolio is None:
        portfolio = Portfolio(name="P", base_currency="COP")
        db_of.add(portfolio)
        db_of.flush()
    db_of.add(Transaction(
        portfolio_id=portfolio.id, asset_id=asset.id, type=TransactionType.BUY,
        executed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=1),
        quantity=D("1000000"), price=D("1"), fees=D("0"),
        currency="COP", fx_rate_to_base=D("1"),
    ))
    db_of.commit()

    respuesta = client.delete("/api/fixed-income/CDT-Y?confirm=true")
    assert respuesta.status_code == 409
    assert "operación" in respuesta.json()["detail"]


def test_deleting_requires_confirmation(client):
    client.post("/api/fixed-income", json={
        "symbol": "CDT-Z", "issuer": "Banco", "annual_rate_pct": "12",
        "issued_on": "2026-01-01", "matures_on": "2027-01-01",
    })
    assert client.delete("/api/fixed-income/CDT-Z").status_code == 409
    assert client.delete("/api/fixed-income/CDT-Z?confirm=true").status_code == 204
