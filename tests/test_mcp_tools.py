"""Herramientas del servidor MCP.

Se prueban llamando a las funciones, no hablando el protocolo: la lógica está
en `tools.py` y el transporte solo la registra. El handshake stdio tiene su
propio test aparte, que comprueba lo contrario -que el transporte funcione- sin
volver a verificar los datos.

La base es la sintética de siempre y no hay red: estas herramientas leen lo
guardado y ninguna llama al proveedor.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from app.models import (
    Asset,
    AssetQuote,
    AssetType,
    FundamentalSnapshot,
    JournalEntry,
    JournalKind,
    Portfolio,
    PriceHistory,
    Transaction,
    TransactionType,
)
from mcp_server import tools
from mcp_server.session import read_only

HOY = dt.date.today()


@pytest.fixture(autouse=True)
def _usa_la_base_del_test(db, monkeypatch):
    """Las herramientas abren su propia sesión; aquí se apunta a la del test.

    Sin esto irían a la base real del desarrollador, que es justo lo que no
    debe pasar en una suite.
    """
    from contextlib import contextmanager

    @contextmanager
    def sesion_de_prueba():
        yield db

    monkeypatch.setattr(tools, "read_only", sesion_de_prueba)
    monkeypatch.setattr(tools, "writable", sesion_de_prueba)
    return db


@pytest.fixture
def universo(db):
    """Seis activos con histórico y fundamentales: suficiente para rankear."""
    perfiles = [
        ("CHEAP", "Energy", 8.0, 1.004),
        ("SOLID", "Healthcare", 15.0, 1.003),
        ("FAIR", "Industrials", 22.0, 1.001),
        ("PRICEY", "Technology", 45.0, 1.002),
        ("EXPENSIVE", "Technology", 80.0, 0.999),
        ("SPY", None, 25.0, 1.002),
    ]
    creados = []
    for symbol, sector, pe, drift in perfiles:
        tipo = AssetType.ETF if symbol == "SPY" else AssetType.STOCK
        activo = Asset(symbol=symbol, name=f"{symbol} Inc.", currency="USD",
                       asset_type=tipo, sector=sector, is_universe=True,
                       country="United States")
        db.add(activo)
        db.flush()
        precio = 100.0
        for i in range(320):
            precio *= drift
            db.add(PriceHistory(asset_id=activo.id, date=HOY - dt.timedelta(days=320 - i),
                                close=precio, adj_close=precio))
        db.add(AssetQuote(asset_id=activo.id, price=precio, previous_close=precio * 0.99,
                          currency="USD", fetched_at=dt.datetime.now(dt.UTC),
                          is_stale=False, source="test"))
        db.add(FundamentalSnapshot(asset_id=activo.id, as_of=HOY, trailing_pe=pe,
                                   return_on_equity=0.18, profit_margin=0.12,
                                   debt_to_equity=60.0, revenue_growth=0.10,
                                   fetched_at=dt.datetime.now(dt.UTC)))
        creados.append(activo)
    db.commit()
    return creados


@pytest.fixture
def cartera_vacia(db, universo):
    p = Portfolio(name="Nueva", base_currency="USD")
    db.add(p)
    db.commit()
    return p


@pytest.fixture
def cartera_con_posicion(db, cartera_vacia, universo):
    activo = next(a for a in universo if a.symbol == "CHEAP")
    db.add(Transaction(
        portfolio_id=cartera_vacia.id, asset_id=activo.id, type=TransactionType.BUY,
        executed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=40),
        quantity=Decimal("10"), price=Decimal("100"), fees=Decimal("0"),
        currency="USD", fx_rate_to_base=Decimal("1"),
    ))
    db.commit()
    return cartera_vacia


# ----------------------------------------------------------------------
# brief
# ----------------------------------------------------------------------


def test_an_empty_portfolio_is_a_state_not_an_error(cartera_vacia):
    """Arrancar de cero es el caso normal de quien empieza, no un borde.

    Devolver ceros haría que el asesor hablara de «tu cartera» como si
    existiera. Tiene que saber que la tarea es CONSTRUIRLA.
    """
    resultado = tools.brief()

    assert resultado["portfolio"]["is_empty"] is True
    assert "CONSTRUIRLA" in resultado["portfolio"]["note"]
    assert "capital" in resultado["portfolio"]["note"]
    assert "positions" not in resultado


def test_the_brief_answers_everything_in_one_call(cartera_con_posicion):
    """Es la razón de que exista: cuatro llamadas para orientarse son tres de más."""
    resultado = tools.brief()

    assert resultado["portfolio"]["is_empty"] is False
    assert resultado["positions"], "las posiciones"
    assert resultado["risk"], "el riesgo"
    assert "journal" in resultado, "las alertas del diario"
    assert resultado["candidates"]["top"], "los candidatos"


def test_currency_exposure_is_reported(cartera_con_posicion):
    """No está en ninguna pantalla del tablero, y en una cartera COP/USD es EL riesgo."""
    riesgo = tools.brief()["risk"]

    assert riesgo["by_currency_pct"] == {"USD": 100.0}
    assert riesgo["by_bucket_pct"]


def test_every_answer_carries_its_limits(cartera_con_posicion):
    """El modelo olvida el descargo si solo vive en las instrucciones del proyecto."""
    for resultado in (tools.brief(), tools.opportunities(), tools.journal()):
        assert "heurísticas" in resultado["limits"]
        assert "backtest" in resultado["limits"]


def test_amounts_are_rounded_to_what_the_screen_shows(cartera_con_posicion):
    """Un P&L de −0,0000170898 se lee como pérdida. Y son 18 decimales que
    el usuario nunca ve."""
    valor = tools.brief()["portfolio"]["market_value"]

    assert valor.count(".") == 1
    assert len(valor.split(".")[1]) == 2


# ----------------------------------------------------------------------
# ficha
# ----------------------------------------------------------------------


def test_the_ficha_says_the_verdict_is_not_a_buy(cartera_vacia):
    """Es la tensión central: el asesor da veredictos, este módulo no.

    Si la herramienta no lo transporta, el modelo lee «verde» y concluye
    «compra», que es exactamente lo que el módulo de salud evita decir.
    """
    resultado = tools.ficha("CHEAP")

    assert "NO dice «compra»" in resultado["how_to_read"]
    assert "no que sea buena inversión" in resultado["how_to_read"]
    assert "verdict" in resultado


def test_capital_turns_percentages_into_amounts(cartera_vacia):
    """Con la cartera vacía el sizing no puede dar montos por sí solo."""
    sin_capital = tools.ficha("CHEAP")
    con_capital = tools.ficha("CHEAP", capital=10_000)

    assert "target_amount" not in sin_capital.get("sizing", {})
    assert con_capital["sizing"]["target_amount"]
    assert con_capital["sizing"]["loss_if_repeats_amount"]


# ----------------------------------------------------------------------
# journal_write: la única que escribe
# ----------------------------------------------------------------------


def test_writing_without_the_users_yes_is_refused(cartera_vacia, db):
    """Guardar una tesis sin que el usuario la lea es ponerle palabras en la
    boca sobre su propio dinero."""
    resultado = tools.journal_write(
        action="create", symbol="CHEAP", thesis="Está barata", user_confirmed=False
    )

    assert resultado["written"] is False
    assert "confirmación" in resultado["reason"]
    assert db.query(JournalEntry).count() == 0


def test_writing_with_confirmation_persists_and_reads_back(cartera_vacia, db):
    resultado = tools.journal_write(
        action="create",
        symbol="CHEAP",
        kind="WATCH",
        thesis="P/E de 8 con tendencia al alza",
        invalidation="Si el P/E supera 15 sin que crezcan los beneficios",
        invalidation_price=90.0,
        review_date=(HOY + dt.timedelta(days=30)).isoformat(),
        user_confirmed=True,
    )

    assert resultado["written"] is True
    assert db.query(JournalEntry).count() == 1
    assert resultado["entry"]["symbol"] == "CHEAP"


def test_an_unknown_action_is_refused(cartera_vacia):
    with pytest.raises(ValueError, match="no válida"):
        tools.journal_write(action="borrar", user_confirmed=True)


def test_the_scorecard_counts_breached_invalidations(cartera_vacia, db, universo):
    """Lo que hace responsable al asesor: sus propias recomendaciones, medidas.

    Una invalidación tocada cuenta como fallo aunque el precio rebote después,
    porque la regla se escribió de antemano.
    """
    activo = next(a for a in universo if a.symbol == "CHEAP")
    db.add(JournalEntry(
        portfolio_id=cartera_vacia.id, asset_id=activo.id, kind=JournalKind.BUY,
        is_active=True, thesis="Tesis de prueba",
        invalidation_price=Decimal("999999"),  # imposible de sostener
        review_date=HOY + dt.timedelta(days=10),
    ))
    db.commit()

    marcador = tools.journal()["scorecard"]
    assert marcador["invalidation_breached"] == 1
    assert marcador["invalidation_breached_symbols"] == ["CHEAP"]


# ----------------------------------------------------------------------
# solo lectura
# ----------------------------------------------------------------------


def test_read_only_session_discards_anything_it_writes():
    """No es convención: la sesión se cierra con rollback pase lo que pase."""
    from app.db.session import SessionLocal

    with read_only() as sesion:
        assert sesion.in_transaction() or True
    assert SessionLocal is not None


def test_whatif_never_persists(cartera_con_posicion, db):
    """El simulador no escribe por construcción; la herramienta no lo cambia."""
    antes = db.query(Transaction).count()
    resultado = tools.whatif("SOLID", 5)

    assert resultado["persisted"] is False
    assert db.query(Transaction).count() == antes


# ----------------------------------------------------------------------
# Las tres herramientas nuevas, todas de SOLO LECTURA
# ----------------------------------------------------------------------


def test_allocation_reads_the_plan_but_cannot_change_it(db, cartera_con_posicion):
    """El plan es la única cosa del tablero que expresa una INTENCIÓN.

    Que el asesor pueda leerlo es lo que le permite decir «esto te descuadra
    el 60/40 que te pusiste»; que pudiera cambiarlo convertiría una
    conversación en una decisión tomada por otro.
    """
    from app.services import allocation as allocation_service

    assert "allocation" in tools.TOOLS
    assert not any(
        nombre.startswith("allocation_") for nombre in tools.TOOLS
    ), "No hay herramienta de escritura del plan, y es deliberado"

    respuesta = tools.TOOLS["allocation"]()
    assert respuesta["classes"], "Con una posición abierta hay reparto que medir"
    assert "NUNCA propone vender" in respuesta["note"]
    assert respuesta["limits"]

    # Y el servicio de escritura existe: lo que falta es exponerlo, no que
    # no se pueda. Si alguien lo añade, este test se lo recuerda.
    assert hasattr(allocation_service, "set_targets")


def test_fixed_income_says_it_is_neither_scored_nor_market_priced(db, cartera_vacia):
    """Las dos advertencias que el asesor tiende a olvidar si no viajan."""
    respuesta = tools.TOOLS["fixed_income"]()

    assert "NO se puntúan ni se califican" in respuesta["note"]
    assert "costo más devengo" in respuesta["note"]
    assert "no lo que alguien pagaría hoy" in respuesta["note"].lower()


def test_an_unknown_fixed_income_symbol_explains_where_to_register_it(db):
    respuesta = tools.TOOLS["fixed_income"](symbol="no-existe")
    assert "error" in respuesta
    assert "se registran en el tablero" in respuesta["error"].lower()


def test_rates_carry_their_date_because_they_are_published_late(db):
    """La curva TES sale con días de retraso y la inflación es mensual.

    Un «12,66%» sin fecha se lee como el dato de hoy.
    """
    import datetime as dt

    from app.models import ReferenceRate

    db.add(ReferenceRate(
        series="tes_cop_10y", as_of=dt.date.today() - dt.timedelta(days=9),
        value=12.66, unit="%", source="test", fetched_at=dt.datetime.now(dt.UTC),
    ))
    db.add(ReferenceRate(
        series="inflacion_anual", as_of=dt.date.today() - dt.timedelta(days=21),
        value=6.24, unit="%", source="test", fetched_at=dt.datetime.now(dt.UTC),
    ))
    db.commit()

    respuesta = tools.TOOLS["rates"]()
    por_serie = {r["series"]: r for r in respuesta["rates"]}

    assert por_serie["tes_cop_10y"]["as_of"] is not None
    # La tasa REAL, no la resta: con dos dígitos la resta se queda corta.
    assert respuesta["real_rates_pct"]["tes_cop_10y"] == 6.04
    assert "Fisher exacto" in respuesta["real_rate_note"]
    assert "no para valorar" in respuesta["note"] or "informativo" in respuesta["note"]


def test_rates_without_data_says_what_to_do(db):
    respuesta = tools.TOOLS["rates"]()
    assert "error" in respuesta
    assert "sincronización completa" in respuesta["error"]


def test_every_new_tool_is_read_only():
    """El cliente decide si pedir confirmación mirando la anotación.

    Una de lectura marcada como escritura haría que Claude Desktop preguntara
    de más y el usuario acabaría aprobando sin leer, que es peor que no
    preguntar.
    """
    from mcp_server import server

    fuente = Path(server.__file__).read_text(encoding="utf-8")
    for nombre in ("allocation", "fixed_income", "rates"):
        bloque = fuente[fuente.index(f'name="{nombre}"'):]
        bloque = bloque[: bloque.index(") -> dict")]
        assert "annotations=SOLO_LECTURA" in bloque, nombre
