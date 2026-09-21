"""Las herramientas del asesor. Cada una envuelve servicios existentes.

AQUÍ NO SE CALCULA NADA. Si una cifra no la sabe producir `app/services`,
tampoco la produce el asesor: esa es la frontera que mantiene auditable el
tablero y desmontable la capa de IA.

POCAS Y GORDAS, no muchas y finas. Con un catálogo largo el modelo gasta
turnos decidiendo qué llamar y elige mal; `brief` responde de una vez lo que
hacen falta cuatro llamadas para reunir.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.exceptions import TableroError
from app.models import Asset, AssetType, Portfolio
from app.repositories import portfolio as portfolio_repo
from app.schemas.journal import JournalCreate, JournalReview, JournalUpdate
from app.services import allocation as allocation_service
from app.services import ficha as ficha_service
from app.services import fixed_income as fixed_income_service
from app.services import journal as journal_service
from app.services import opportunities as opportunity_service
from app.services import performance as performance_service
from app.services import portfolio as portfolio_service
from app.services import reference_rates as rates_service
from app.services import simulation as simulation_service
from app.services import universe as universe_service
from app.services.grading import parse_quality_tiers
from app.services.regions import parse_regions
from mcp_server import shaping
from mcp_server.session import read_only, writable

# Aviso que viaja en TODAS las respuestas. No es letra pequeña: es la
# diferencia entre un asesor y un oráculo, y el modelo tiende a olvidarlo si
# solo está en las instrucciones del proyecto.
LIMITS = (
    "El Opportunity Score y las banderas son heurísticas SIN backtest. Los "
    "datos vienen de Yahoo Finance con retraso y errores posibles. Nada de "
    "esto es una recomendación de inversión."
)


def readable_errors(funcion):
    """Devuelve los errores de dominio COMO DATOS, no como excepción.

    El SDK convierte una excepción en «Error executing tool <nombre>» y se
    traga el mensaje. Para un asesor eso es inservible: la diferencia entre
    «no hay ningún portafolio, créalo en el tablero» y un error opaco es la
    diferencia entre decirle al usuario qué hacer y decirle que algo falló.

    Solo se capturan los errores de dominio y `ValueError`, que son los que
    describen una situación del usuario. Un fallo inesperado SIGUE lanzando:
    es un defecto del servidor y tiene que verse como tal, no disfrazarse de
    respuesta.
    """
    import functools

    @functools.wraps(funcion)
    def envoltorio(*args, **kwargs):
        try:
            return funcion(*args, **kwargs)
        except OperationalError as exc:
            # Tabla ausente: la base está por detrás de las migraciones. Le
            # pasó de verdad al usuario y el síntoma era un 500 opaco desde
            # el navegador; aquí sería un «Error executing tool» igual de
            # inútil. Merece decir exactamente qué comando resuelve.
            falta = "no such table" in str(exc).lower()
            return {
                "error": str(exc.orig) if exc.orig else str(exc),
                "tool": funcion.__name__,
                "hint": (
                    "La base de datos está por detrás del código: falta una "
                    "tabla. Dile al usuario que ejecute `alembic upgrade head` "
                    "en la carpeta del tablero y lo vuelva a intentar."
                    if falta
                    else "No se pudo leer la base de datos. No inventes los datos."
                ),
            }
        except (TableroError, ValueError) as exc:
            return {
                "error": str(exc),
                "tool": funcion.__name__,
                "hint": (
                    "Cuéntale al usuario qué falta y qué puede hacer. No "
                    "inventes los datos que no llegaron."
                ),
            }

    return envoltorio


def _portfolio(db: Session, portfolio_id: int | None) -> Portfolio:
    """El portafolio pedido, o el único que haya.

    Con un solo portafolio -el caso normal- obligar al modelo a averiguar su id
    gasta un turno para nada.
    """
    if portfolio_id is not None:
        return portfolio_repo.get_portfolio(db, portfolio_id)
    existing = portfolio_repo.list_portfolios(db)
    if not existing:
        raise ValueError(
            "No hay ningún portafolio todavía. Créalo en el tablero antes de pedir consejo."
        )
    return existing[0]


# ---------------------------------------------------------------------------
# brief
# ---------------------------------------------------------------------------


def brief(portfolio_id: int | None = None, top: int = 5) -> dict[str, Any]:
    """Cartera, riesgo, alertas del diario, frescura y mejores candidatos.

    Es la herramienta de entrada: responde «¿cómo estoy y qué mirar hoy?» en
    una llamada.

    CON LA CARTERA VACÍA no falla ni devuelve ceros: dice que está vacía y que
    la tarea es CONSTRUIR, no optimizar. Es un estado distinto, no un caso
    borde, y el asesor tiene que tratarlo distinto.
    """
    with read_only() as db:
        portfolio = _portfolio(db, portfolio_id)
        summary = portfolio_service.get_summary(db, portfolio)
        empty = not summary.positions

        payload: dict[str, Any] = {
            "as_of": dt.datetime.now(dt.UTC),
            "portfolio": {
                "id": portfolio.id,
                "name": portfolio.name,
                "base_currency": portfolio.base_currency,
                "is_empty": empty,
            },
            "limits": LIMITS,
        }

        if empty:
            payload["portfolio"]["note"] = (
                "La cartera no tiene posiciones. La tarea es CONSTRUIRLA: cuánto "
                "capital desplegar, en qué orden y en cuántas entradas. Pregunta "
                "al usuario de cuánto dispone antes de dimensionar nada, porque "
                "sin capital el sizing solo puede dar porcentajes."
            )
        else:
            payload["portfolio"].update({
                "market_value": shaping.money(summary.market_value),
                "cash": shaping.money(summary.cash_balance),
                "total_cost": shaping.money(summary.total_cost),
                "unrealized_pnl": shaping.money(summary.unrealized_pnl),
                "realized_pnl": shaping.money(summary.realized_pnl),
                "dividends": shaping.money(summary.dividend_income),
                "return_pct": shaping.pct(summary.total_return_pct),
                "has_stale_prices": summary.has_stale_prices,
                "positions_without_price": summary.positions_without_price,
            })
            if summary.asset_pnl is not None:
                payload["portfolio"]["attribution"] = {
                    "from_asset": shaping.money(summary.asset_pnl),
                    "from_currency": shaping.money(summary.fx_pnl),
                    "note": (
                        "Las dos cifras suman el P&L no realizado. Separan lo que "
                        "hizo la empresa de lo que hizo el tipo de cambio."
                    ),
                }
            payload["positions"] = [
                {
                    "symbol": p.symbol,
                    "name": p.name,
                    "quantity": shaping.money(p.quantity, 6),
                    "currency": p.currency,
                    "bucket": p.exposure_bucket,
                    "weight_pct": shaping.pct(p.weight_pct),
                    "market_value": shaping.money(p.market_value),
                    "average_cost": shaping.money(p.average_cost),
                    "current_price": shaping.money(p.current_price),
                    "unrealized_pnl": shaping.money(p.unrealized_pnl),
                    "unrealized_return_pct": shaping.pct(p.unrealized_return_pct),
                    "is_stale": p.is_stale,
                }
                for p in summary.positions
            ]
            payload["risk"] = _risk(summary)

        payload["journal"] = _journal_alerts(db, portfolio)
        payload["candidates"] = _top_candidates(db, portfolio, top)
        return shaping.compact(payload)


def _risk(summary) -> dict[str, Any]:
    """Concentración y riesgo. Incluye la exposición por DIVISA, que no está
    en ninguna pantalla del tablero y en una cartera COP/USD es el riesgo
    principal."""
    total = sum((p.market_value for p in summary.positions if p.market_value), Decimal(0))
    by_currency: dict[str, Decimal] = {}
    by_bucket: dict[str, Decimal] = {}
    for position in summary.positions:
        if position.market_value is None:
            continue
        by_currency[position.currency] = (
            by_currency.get(position.currency, Decimal(0)) + position.market_value
        )
        bucket = position.exposure_bucket or "Desconocido"
        by_bucket[bucket] = by_bucket.get(bucket, Decimal(0)) + position.market_value

    def share(values: dict[str, Decimal]) -> dict[str, float]:
        if total <= 0:
            return {}
        return {k: shaping.pct(v / total * 100) for k, v in sorted(
            values.items(), key=lambda kv: kv[1], reverse=True
        )}

    return {
        "diversification_index": shaping.pct(summary.diversification_index),
        "top_position_pct": shaping.pct(summary.top_position_pct),
        "annualized_volatility_pct": shaping.pct(summary.annualized_volatility_pct),
        "max_drawdown_pct": shaping.pct(summary.max_drawdown_pct),
        "by_bucket_pct": share(by_bucket),
        "by_currency_pct": share(by_currency),
        "note": (
            "Diversificación es 100·(1−HHI) por cubo de exposición: escala "
            "relativa, con 11 sectores el techo real ronda 91. Volatilidad y "
            "caída se componen con la correlación real entre posiciones, no "
            "como promedio de las individuales."
        ),
    }


def _journal_alerts(db: Session, portfolio: Portfolio) -> dict[str, Any]:
    entries = journal_service.list_entries(db, portfolio)
    con_alerta = [e for e in entries.entries if e.alerts]
    return {
        "active": entries.counts.active,
        "with_alerts": entries.counts.with_alerts,
        "alerts": [
            {
                "symbol": e.symbol,
                "kind": e.kind,
                "review_date": shaping.day(e.review_date),
                "thesis": e.thesis,
                "invalidation": e.invalidation,
                "items": [{"level": a.level, "text": a.text} for a in e.alerts],
            }
            for e in con_alerta
        ],
    }


def _top_candidates(db: Session, portfolio: Portfolio, top: int) -> dict[str, Any]:
    assets = universe_service.get_ingestion_universe(db)
    try:
        response = opportunity_service.compute_opportunities(
            db, portfolio, assets=assets, limit=max(1, min(top, 20))
        )
    except Exception as exc:  # universo insuficiente u otro fallo de datos
        return {"unavailable": str(exc)}
    return {
        "universe_size": response.universe_size,
        "benchmark": response.benchmark_symbol,
        "quality_warning": response.universe_quality_warning,
        "freshness": shaping.compact(response.freshness.model_dump())
        if response.freshness else None,
        "top": [
            {
                "rank": r.rank,
                # LA CLASE Y EL TAMAÑO VAN CON EL PUESTO, siempre. El puesto
                # es dentro de la clase, así que un «#6» de renta fija y un
                # «#12» de acciones no son comparables: sin estos dos campos
                # el asesor los ordena como si lo fueran.
                "asset_class": r.asset_class,
                "class_size": r.class_size,
                "symbol": r.symbol,
                "name": r.name,
                "score": r.score,
                "grade": r.assessment.grade,
                "grade_label": r.assessment.label,
                "region": r.market_region,
                "price": shaping.money(r.current_price),
                "confidence": r.confidence,
            }
            for r in response.opportunities
        ],
        "warnings": response.warnings,
    }


# ---------------------------------------------------------------------------
# ficha
# ---------------------------------------------------------------------------


def ficha(
    symbol: str, portfolio_id: int | None = None, capital: float | None = None
) -> dict[str, Any]:
    """La ficha de compra completa de UNA empresa, contra tu cartera real.

    Trae dentro el score y su desglose, el veredicto de banderas, la salud
    financiera, los pares de su sector, lo que ya tienes de ese activo y el
    tamaño de posición sugerido. Por eso no hay una herramienta `sizing`
    aparte: se consumiría siempre junto con esto.

    `capital` sirve cuando la cartera está vacía: sin él, el sizing solo puede
    dar porcentajes y no montos.
    """
    with read_only() as db:
        portfolio = _portfolio(db, portfolio_id)
        data = ficha_service.build_ficha(db, portfolio, symbol)
        payload = data.model_dump(mode="json")

        if capital is not None and capital > 0:
            payload["sizing"] = _resize(data, Decimal(str(capital)), portfolio)

        payload["limits"] = LIMITS
        payload["how_to_read"] = (
            "El veredicto NO dice «compra»: dice si este filtro encontró "
            "problemas. Sin banderas rojas significa que no se hallaron, no que "
            "sea buena inversión. La calificación A–E sí es absoluta; el score "
            "es un puesto relativo dentro del universo evaluado."
        )
        return shaping.compact(payload)


def _resize(data, capital: Decimal, portfolio: Portfolio) -> dict[str, Any]:
    """Recalcula los montos del sizing con el capital que declare el usuario.

    Se reusa `size_position` con los mismos parámetros que produjo la ficha, en
    vez de multiplicar porcentajes por fuera: las notas y el binding tienen que
    salir del mismo sitio o dirían cosas distintas.
    """
    from app.services.asset_class import AssetClass
    from app.services.sizing import size_position

    actual = data.sizing
    if actual is None:
        return {}
    resultado = size_position(
        max_drawdown=(
            None if actual.observed_drawdown_pct is None
            else actual.observed_drawdown_pct / 100
        ),
        asset_class=AssetClass(actual.asset_class),
        risk_budget_pct=actual.risk_budget_pct,
        max_position_pct=actual.max_position_pct,
        current_weight_pct=actual.current_pct,
        capital=capital,
        currency_differs=data.currency.upper() != portfolio.base_currency.upper(),
    )
    return shaping.compact({
        "risk_budget_pct": resultado.risk_budget_pct,
        "target_pct": shaping.pct(resultado.target_pct),
        "add_pct": shaping.pct(resultado.add_pct),
        "stress_loss_pct": shaping.pct(resultado.stress_loss_pct),
        "capital": shaping.money(resultado.capital),
        "target_amount": shaping.money(resultado.target_amount),
        "add_amount": shaping.money(resultado.add_amount),
        "loss_if_repeats_amount": shaping.money(resultado.loss_if_repeats_amount),
        "base_currency": portfolio.base_currency,
        "notes": resultado.notes,
    })


# ---------------------------------------------------------------------------
# opportunities
# ---------------------------------------------------------------------------


def opportunities(
    portfolio_id: int | None = None,
    limit: int = 15,
    regions: str | None = None,
    quality_tiers: str | None = None,
) -> dict[str, Any]:
    """Ranking completo con filtros de región y calificación.

    Los filtros se aplican DESPUÉS de puntuar: el score es un percentil dentro
    del universo entero, así que `rank` sigue siendo la posición global. Ver
    que el mejor colombiano es el #32 de 494 es información.
    """
    with read_only() as db:
        portfolio = _portfolio(db, portfolio_id)
        assets = universe_service.get_ingestion_universe(db)
        response = opportunity_service.compute_opportunities(
            db,
            portfolio,
            assets=assets,
            limit=max(1, min(limit, 50)),
            regions=parse_regions(regions),
            quality_tiers=parse_quality_tiers(quality_tiers),
        )
        return shaping.compact({
            "as_of": response.as_of,
            "universe_size": response.universe_size,
            "matched_size": response.matched_size,
            "formula": response.formula,
            "benchmark": {"symbol": response.benchmark_symbol, "pe": response.benchmark_pe},
            "grade_counts": response.grade_counts,
            # Cuántos hay EN CADA CLASE. Sin esto, un «#6 de 31» no se puede
            # situar: el asesor no sabe si 31 son muchos o si esa clase apenas
            # tiene candidatos y el puesto discrimina poco.
            "class_counts": response.class_counts,
            "region_counts": response.region_counts,
            "quality_warning": response.universe_quality_warning,
            "freshness": response.freshness.model_dump(mode="json")
            if response.freshness else None,
            "rows": [
                {
                    "rank": r.rank,
                    "asset_class": r.asset_class,
                    "class_size": r.class_size,
                    "symbol": r.symbol,
                    "name": r.name,
                    "sector": r.sector,
                    "region": r.market_region,
                    "score": r.score,
                    "grade": r.assessment.grade,
                    "grade_label": r.assessment.label,
                    "price": shaping.money(r.current_price),
                    "currency": r.currency,
                    "confidence": r.confidence,
                    "factors": {
                        "value": r.value.score,
                        "momentum": r.momentum.score,
                        "diversification": r.diversification.score,
                        "diversification_measured": r.diversification.available,
                        "risk": r.risk.score,
                    },
                    "notes": r.notes,
                }
                for r in response.opportunities
            ],
            "warnings": response.warnings,
            "limits": LIMITS,
        })


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------


def journal(portfolio_id: int | None = None, include_archived: bool = False) -> dict[str, Any]:
    """Tus tesis con sus alertas, más el MARCADOR DEL ASESOR.

    El marcador es lo que hace responsable al asesor: cuántas decisiones suyas
    hay anotadas, cuántas tocaron su nivel de invalidación y cómo han ido. Sin
    esto, cada conversación empieza con la misma confianza sin importar cómo
    salió la anterior.
    """
    with read_only() as db:
        portfolio = _portfolio(db, portfolio_id)
        listado = journal_service.list_entries(
            db, portfolio, include_archived=include_archived
        )
        entries = [e.model_dump(mode="json") for e in listado.entries]
        return shaping.compact({
            "as_of": listado.as_of,
            "counts": listado.counts.model_dump(mode="json"),
            "entries": entries,
            "scorecard": _scorecard(listado.entries),
            "limits": LIMITS,
        })


def _scorecard(entries) -> dict[str, Any]:
    """Rendimiento de lo anotado, medido contra su propio precio de entrada.

    Deliberadamente sobrio: cuenta lo que pasó, no lo interpreta. Una entrada
    cuya invalidación se tocó es un fallo aunque el precio haya rebotado
    después, porque la regla la escribió uno mismo de antemano.
    """
    activas = [e for e in entries if e.is_active]
    invalidadas = [
        e for e in activas
        if any(a.code == "invalidation_breached" for a in e.alerts)
    ]
    bajaron = [e for e in activas if any(a.code == "grade_dropped" for a in e.alerts)]
    vencidas = [e for e in activas if any(a.code == "review_overdue" for a in e.alerts)]
    return {
        "entries_total": len(entries),
        "active": len(activas),
        "invalidation_breached": len(invalidadas),
        "invalidation_breached_symbols": [e.symbol for e in invalidadas],
        "grade_dropped": len(bajaron),
        "grade_dropped_symbols": [e.symbol for e in bajaron],
        "reviews_overdue": len(vencidas),
        "note": (
            "Una invalidación tocada cuenta como fallo aunque el precio haya "
            "rebotado: la regla se escribió de antemano. Revísalas antes de "
            "recomendar nada nuevo."
        ),
    }


# ---------------------------------------------------------------------------
# journal_write  -- LA ÚNICA QUE ESCRIBE
# ---------------------------------------------------------------------------


def journal_write(
    action: str,
    symbol: str | None = None,
    kind: str | None = None,
    thesis: str | None = None,
    invalidation: str | None = None,
    invalidation_price: float | None = None,
    review_date: str | None = None,
    entry_id: int | None = None,
    review_note: str | None = None,
    user_confirmed: bool = False,
    portfolio_id: int | None = None,
) -> dict[str, Any]:
    """Crea, actualiza o revisa una entrada del diario. ESCRIBE en la base.

    `user_confirmed` tiene que llegar en True y NO puede darse por supuesto: el
    asesor debe enseñar el texto exacto que va a guardar y esperar un sí. Es la
    única puerta de escritura de todo el servidor, y una tesis guardada sin
    que el usuario la lea es poner palabras en su boca sobre su propio dinero.

    Para qué existe: lo que el asesor recomienda queda FALSABLE. En la
    siguiente sesión sus propias alertas le devuelven el resultado. Un asesor
    que solo lee nunca rinde cuentas.
    """
    if not user_confirmed:
        return {
            "written": False,
            "reason": (
                "Falta la confirmación del usuario. Muéstrale el texto exacto de "
                "la tesis, la invalidación y la fecha de revisión, y vuelve a "
                "llamar con user_confirmed=true solo cuando te diga que sí."
            ),
        }

    accion = action.lower().strip()
    with writable() as db:
        portfolio = _portfolio(db, portfolio_id)

        if accion == "create":
            if not (symbol and thesis):
                raise ValueError("Para crear hacen falta `symbol` y `thesis`.")
            payload = JournalCreate(
                symbol=symbol,
                kind=kind or "WATCH",
                thesis=thesis,
                invalidation=invalidation,
                invalidation_price=(
                    None if invalidation_price is None else Decimal(str(invalidation_price))
                ),
                review_date=dt.date.fromisoformat(review_date) if review_date else None,
            )
            entry = journal_service.create_entry(db, portfolio, payload)

        elif accion == "update":
            if entry_id is None:
                raise ValueError("Para actualizar hace falta `entry_id`.")
            entry = journal_service.update_entry(
                db,
                entry_id,
                JournalUpdate(
                    kind=kind,
                    thesis=thesis,
                    invalidation=invalidation,
                    invalidation_price=(
                        None if invalidation_price is None
                        else Decimal(str(invalidation_price))
                    ),
                    review_date=dt.date.fromisoformat(review_date) if review_date else None,
                ),
            )

        elif accion == "review":
            if entry_id is None:
                raise ValueError("Para revisar hace falta `entry_id`.")
            entry = journal_service.review_entry(
                db, entry_id, JournalReview(note=review_note)
            )

        else:
            raise ValueError(f"Acción «{action}» no válida: usa create, update o review.")

        leida = journal_service.read_entry(db, entry, portfolio)
        return shaping.compact({
            "written": True,
            "action": accion,
            "entry": leida.model_dump(mode="json"),
        })


# ---------------------------------------------------------------------------
# whatif
# ---------------------------------------------------------------------------


def whatif(
    symbol: str,
    quantity: float,
    price: float | None = None,
    portfolio_id: int | None = None,
) -> dict[str, Any]:
    """Simula una compra: antes y después, sin escribir NADA.

    El simulador no persiste por construcción -las transacciones simuladas
    nunca pasan por `db.add()`- así que esta herramienta es de solo lectura
    aunque hable de operar.
    """
    from app.repositories import market as market_repo
    from app.schemas.simulation import SimulatedTrade, SimulationRequest

    with read_only() as db:
        portfolio = _portfolio(db, portfolio_id)

        # Sin precio se usa el de AHORA, igual que hace el simulador de la
        # interfaz: una simulación es siempre «si lo hiciera hoy». En el alta
        # de una transacción REAL no se prellena, porque ahí la fecha la elige
        # el usuario y el precio de hoy sería un dato falso.
        efectivo = None if price is None else Decimal(str(price))
        if efectivo is None:
            activo = market_repo.get_assets_by_symbols(db, [symbol]).get(symbol.upper())
            cotizacion = (
                market_repo.get_quotes(db, [activo.id]).get(activo.id) if activo else None
            )
            if cotizacion is None:
                raise ValueError(
                    f"No hay precio guardado para {symbol.upper()}: indica `price` "
                    "o sincroniza los datos de mercado."
                )
            # Se acota a 8 decimales, que es lo que admite el esquema. Las
            # cotizaciones llegan con ruido de coma flotante -EQNR cotiza a
            # 44.09000015258789, no a 44,09- y el validador las rechaza por
            # exceso de decimales, no por el valor.
            efectivo = Decimal(str(cotizacion.price)).quantize(Decimal("1.00000000"))

        peticion = SimulationRequest(trades=[
            SimulatedTrade(
                symbol=symbol,
                type="BUY",
                quantity=Decimal(str(quantity)),
                price=efectivo,
            )
        ])
        resultado = simulation_service.simulate(db, portfolio, peticion)
        payload = shaping.round_amounts(resultado.model_dump(mode="json"))
        payload["limits"] = LIMITS
        payload["how_to_read"] = (
            "`persisted` es siempre false: el simulador no escribe nada por "
            "construcción. Compara `current_state` con `simulated_state`."
        )
        return shaping.compact(payload)


# ---------------------------------------------------------------------------
# performance (va dentro de `brief` cuando hay cartera; suelta para el detalle)
# ---------------------------------------------------------------------------


def performance(portfolio_id: int | None = None, days: int = 365) -> dict[str, Any]:
    """Curva de valor, comparación con el índice y retorno anualizado."""
    with read_only() as db:
        portfolio = _portfolio(db, portfolio_id)
        serie = performance_service.compute_series(db, portfolio, days=days)
        puntos = serie.points
        return shaping.compact({
            "benchmark": serie.benchmark_symbol,
            "xirr_pct": None if serie.xirr is None else round(serie.xirr * 100, 2),
            "benchmark_xirr_pct": (
                None if serie.benchmark_xirr is None
                else round(serie.benchmark_xirr * 100, 2)
            ),
            "first": shaping.compact({
                "date": puntos[0].date, "total_value": shaping.money(puntos[0].total_value)
            }) if puntos else None,
            "last": shaping.compact({
                "date": puntos[-1].date,
                "total_value": shaping.money(puntos[-1].total_value),
                "net_invested": shaping.money(puntos[-1].net_invested),
                "benchmark_value": shaping.money(puntos[-1].benchmark_value),
            }) if puntos else None,
            "points": len(puntos),
            "warnings": serie.warnings,
            "limits": LIMITS,
        })


# ---------------------------------------------------------------------------
# allocation: el plan de asignación entre clases
# ---------------------------------------------------------------------------


def allocation(
    portfolio_id: int | None = None, contribution: float = 0.0
) -> dict[str, Any]:
    """Peso actual de cada clase frente al plan, y a dónde va el próximo aporte.

    SOLO LECTURA, y eso no es una limitación técnica: el plan es la única cosa
    del tablero que expresa una INTENCIÓN del usuario, no una medición. Que el
    asesor pueda leerlo es lo que le permite decir «esto te descuadra el 60/40
    que te pusiste»; que pudiera cambiarlo convertiría una conversación en una
    decisión tomada por otro.
    """
    with read_only() as db:
        portfolio = _portfolio(db, portfolio_id)
        resumen = portfolio_service.get_summary(db, portfolio)
        informe = allocation_service.build_report(db, portfolio, resumen)
        orden = allocation_service.suggest_contribution(
            informe, Decimal(str(contribution))
        )
        return shaping.compact({
            "portfolio": portfolio.name,
            "base_currency": portfolio.base_currency,
            "has_plan": informe.has_plan,
            "planned_pct": shaping.pct(informe.planned_pct),
            "total_value": shaping.money(informe.total_value),
            "classes": [
                shaping.compact({
                    "asset_class": fila.asset_class,
                    "label": fila.label,
                    "current_pct": shaping.pct(fila.current_pct),
                    "target_pct": shaping.pct(fila.target_pct),
                    "band_pct": shaping.pct(fila.band_pct),
                    "drift_pct": shaping.pct(fila.drift_pct),
                    "status": fila.status,
                    "gap_amount": shaping.money(fila.gap_amount),
                })
                for fila in informe.positions
            ],
            "contribution_order": [p.asset_class for p in orden],
            "warnings": informe.warnings,
            "note": (
                "El plan es del usuario: esta herramienta solo mide la "
                "desviación y NUNCA propone vender. Rebalancear con aportes "
                "nuevos evita realizar ganancias y pagar comisiones."
            ),
            "limits": LIMITS,
        })


# ---------------------------------------------------------------------------
# fixed_income: TES, CDT y FIC cargados a mano
# ---------------------------------------------------------------------------


def fixed_income(symbol: str | None = None) -> dict[str, Any]:
    """Instrumentos de renta fija directa, con las cinco preguntas contestadas.

    NO llevan score ni calificación A-E, y hay que decirlo cada vez: un score
    es un rango percentil contra pares y aquí no hay pares medibles. Cada CDT
    tiene su emisor, su plazo y su tasa.
    """
    with read_only() as db:
        objetivo = (symbol or "").strip().upper()
        activos = db.scalars(
            select(Asset).where(Asset.asset_type == AssetType.FIXED_INCOME)
        ).all()
        if objetivo:
            activos = [a for a in activos if a.symbol == objetivo]
            if not activos:
                return {
                    "error": (
                        f"No hay ningún instrumento de renta fija con símbolo "
                        f"{objetivo}. Se registran en el tablero, no desde aquí."
                    ),
                    "limits": LIMITS,
                }

        salida = []
        for activo in activos:
            condiciones = fixed_income_service.get_terms(db, activo)
            if condiciones is None:
                continue
            evaluacion = fixed_income_service.assess(db, activo, condiciones)
            indices = rates_service.latest(db)
            tasa, procedencia = fixed_income_service.effective_annual_rate(
                condiciones, indices
            )
            devengo = (
                fixed_income_service.accrue(condiciones, tasa)
                if tasa is not None
                else None
            )
            salida.append(shaping.compact({
                "symbol": evaluacion.symbol,
                "issuer": evaluacion.issuer,
                "currency": evaluacion.currency,
                "rate_kind": evaluacion.rate_kind,
                "rate_source": procedencia,
                "nominal_rate_pct": evaluacion.nominal_rate_pct,
                "net_rate_pct": evaluacion.net_rate_pct,
                "real_rate_pct": evaluacion.real_rate_pct,
                "inflation_pct": evaluacion.inflation_pct,
                "inflation_as_of": evaluacion.inflation_as_of,
                "beats_inflation": evaluacion.beats_inflation,
                "market_reference_pct": evaluacion.market_reference_pct,
                "market_reference_label": evaluacion.market_reference_label,
                "beats_market": evaluacion.beats_market,
                "best_bank_rate_pct": evaluacion.best_bank_rate_pct,
                "best_bank_name": evaluacion.best_bank_name,
                "median_bank_rate_pct": evaluacion.median_bank_rate_pct,
                "issuer_market_rate_pct": evaluacion.issuer_market_rate_pct,
                "bank_term_labels": evaluacion.bank_term_labels,
                "days_to_maturity": evaluacion.days_to_maturity,
                "has_secondary_market": evaluacion.has_secondary_market,
                "accrued_factor": None if devengo is None else float(devengo.factor),
                "valuation_basis": None if devengo is None else devengo.basis,
                "valuation_caveat": None if devengo is None else devengo.caveat,
                "questions": evaluacion.questions,
                "warnings": evaluacion.warnings,
            }))

        return shaping.compact({
            "instruments": salida,
            "count": len(salida),
            "note": (
                "NO se puntúan ni se califican: un score es un rango percentil "
                "contra pares y aquí no hay pares. Se valoran a costo más "
                "devengo, que es lo que valen si se llevan a vencimiento, NO lo "
                "que alguien pagaría hoy por ellos."
            ),
            "limits": LIMITS,
        })


# ---------------------------------------------------------------------------
# rates: contra qué se juzga una tasa colombiana
# ---------------------------------------------------------------------------


def rates(term_days: int | None = None, issuer: str | None = None) -> dict[str, Any]:
    """Tasas de referencia del Banco de la República, y CDT por banco.

    Sin esto, un «12,3% anual» no se puede leer: puede ser excelente o estar
    por debajo de la inflación. CADA cifra viaja con su fecha porque la curva
    TES se publica con días de retraso y la inflación es mensual.
    """
    with read_only() as db:
        lecturas = rates_service.latest(db)
        if not lecturas:
            return {
                "error": (
                    "No hay tasas de referencia guardadas. Se traen en la "
                    "sincronización completa; ejecútala desde el tablero."
                ),
                "limits": LIMITS,
            }

        inflacion = lecturas.get("inflacion_anual")
        payload: dict[str, Any] = {
            "rates": [
                shaping.compact({
                    "series": clave,
                    "label": lectura.label,
                    "value": round(lectura.value, 4),
                    "unit": lectura.unit,
                    "as_of": lectura.as_of,
                    "nature": lectura.nature,
                    "stale": lectura.is_stale_monthly,
                })
                for clave, lectura in sorted(lecturas.items())
            ],
            "source": "Banco de la República (SUAMECA)",
            "note": (
                "El propio Banrep advierte que su curva cero cupón TES «es de "
                "tipo informativo y su fin no es la valoración de portafolios»: "
                "sirve para decidir, no para valorar una posición."
            ),
            "limits": LIMITS,
        }

        # SOLO a las NOMINALES. Antes se descontaba la inflación a todo lo que
        # llevara «%», y eso incluía las de UVR, que YA son reales porque el
        # capital se indexa: los TES en UVR salían con 0,0%, −0,9% y −0,05%
        # cuando son 6,24%, 5,28% y 6,19%. Es el doble conteo que el propio
        # CLAUDE.md prohíbe, y estaba a la vista en la respuesta del asesor.
        #
        # También se colaba la META de inflación, cuya «tasa real» no es nada:
        # es la distancia entre dos medidas de inflación.
        if inflacion is not None:
            payload["real_rates_pct"] = {
                clave: round(
                    rates_service.real_rate(lectura.value, inflacion.value), 2
                )
                for clave, lectura in sorted(lecturas.items())
                if lectura.is_nominal_rate
            }
            # Las que ya son reales se dan TAL CUAL y se dice por qué, en vez
            # de omitirlas: quien busca la tasa real a 10 años tiene que
            # encontrarla, y la de UVR es justo esa.
            payload["already_real_pct"] = {
                clave: round(lectura.value, 2)
                for clave, lectura in sorted(lecturas.items())
                if lectura.is_already_real
            }
            payload["real_rate_note"] = (
                f"`real_rates_pct` descuenta por Fisher exacto la inflación del "
                f"{inflacion.as_of.isoformat()} ({inflacion.value}%), no por "
                f"resta: con tasas de dos dígitos la resta se queda corta. "
                f"`already_real_pct` son las de UVR, que YA son reales porque "
                f"el capital se indexa con la inflación: restársela otra vez la "
                f"contaría dos veces."
            )

        if term_days:
            comparacion = rates_service.compare_cdt(
                db, days=term_days, issuer=issuer
            )
            payload["cdt_comparison"] = (
                None
                if comparacion is None
                else shaping.compact({
                    "days": comparacion.days,
                    "term_labels": list(comparacion.term_labels),
                    "best_rate_pct": comparacion.best_rate_pct,
                    "best_entity": comparacion.best_entity,
                    "median_rate_pct": comparacion.median_rate_pct,
                    "issuer_rate_pct": comparacion.issuer_rate_pct,
                    "issuer_name": comparacion.issuer_name,
                    "offers_considered": comparacion.offers_considered,
                    "as_of": comparacion.as_of,
                    "caveat": comparacion.caveat,
                })
            )

        return shaping.compact(payload)


# Se envuelven al exportarlas, no con decorador en cada `def`: así las
# funciones internas siguen lanzando y son fáciles de probar con `pytest.raises`.
TOOLS = {
    nombre: readable_errors(funcion)
    for nombre, funcion in {
        "brief": brief,
        "ficha": ficha,
        "opportunities": opportunities,
        "journal": journal,
        "journal_write": journal_write,
        "whatif": whatif,
        "performance": performance,
        "allocation": allocation,
        "fixed_income": fixed_income,
        "rates": rates,
    }.items()
}
