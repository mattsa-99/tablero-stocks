"""Rendimiento en el tiempo: curva de valor, comparación con un índice y XIRR.

Todo lo de aquí se DERIVA del ledger y de las series guardadas, igual que el
resto del sistema: no hay ninguna tabla de valores históricos que pudiera
desincronizarse del histórico de transacciones.

La diferencia con `services/portfolio.py` es el eje del tiempo. Aquel responde
"¿cuánto vale hoy?" reproduciendo el ledger una vez; este responde "¿cuánto
valía cada día?" reproduciéndolo a lo largo de un calendario.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy.orm import Session

from app.core.money import ZERO, to_decimal
from app.models import Asset, Portfolio, Transaction, TransactionType
from app.repositories import market as market_repo
from app.repositories import portfolio as portfolio_repo

logger = logging.getLogger(__name__)

# Índices de referencia, en orden de preferencia. Se usa el primero que tenga
# serie guardada: pedirlo a la red aquí rompería la regla de que un servicio no
# llama al proveedor.
BENCHMARK_CANDIDATES = ("SPY", "VOO", "IVV", "VTI")

# Días mínimos para anualizar. Anualizar amplifica: sobre 18 días multiplica
# por 20, y la cartera de prueba daba un 129% anual que no significaba nada
# salvo "subió un 4% en tres semanas". Por debajo de un trimestre se devuelve
# None y se dice por qué, en vez de publicar un número impresionante y vacío.
XIRR_MIN_DAYS = 90


@dataclass(frozen=True)
class ValuePoint:
    """La cartera en un día concreto, en divisa base."""

    date: dt.date
    market_value: Decimal
    cash: Decimal
    total_value: Decimal
    # Capital neto aportado hasta ese día (depósitos menos retiradas). Es la
    # línea contra la que se lee la curva: por encima se gana, por debajo se
    # pierde.
    net_invested: Decimal
    # Valor de la misma aportación puesta en el índice. None si no hay serie.
    benchmark_value: Decimal | None = None


@dataclass
class PerformanceSeries:
    points: list[ValuePoint] = field(default_factory=list)
    benchmark_symbol: str | None = None
    warnings: list[str] = field(default_factory=list)
    # Rentabilidad anualizada ponderada por dinero. Ver `xirr`.
    xirr: float | None = None
    benchmark_xirr: float | None = None


class _Forward:
    """Lector de una serie con relleno hacia adelante.

    Los mercados cierran fines de semana y festivos, y no todos los mercados
    cierran los mismos días: la BVC y la NYSE no coinciden. Sin arrastrar el
    último valor conocido, cada festivo de uno abriría un hueco en la curva del
    otro y el valor de la cartera aparecería desplomado ese día.

    Avanza en O(n) sobre el calendario porque se recorre hacia adelante y nunca
    hacia atrás; una búsqueda binaria por punto sería O(n log n) sin necesidad.
    """

    def __init__(self, serie: list[tuple[dt.date, float | Decimal]]) -> None:
        self._serie = serie
        self._index = 0
        self._current: float | Decimal | None = None

    def at(self, day: dt.date) -> float | Decimal | None:
        while self._index < len(self._serie) and self._serie[self._index][0] <= day:
            self._current = self._serie[self._index][1]
            self._index += 1
        return self._current


def _trading_calendar(
    series: dict[int, list[tuple[dt.date, float]]], start: dt.date, end: dt.date
) -> list[dt.date]:
    """Días con cotización de CUALQUIER activo, dentro del rango.

    Se usa la unión y no la intersección: con la intersección, un activo que
    empieza a cotizar tarde recortaría la curva entera de la cartera.
    """
    days: set[dt.date] = set()
    for serie in series.values():
        days.update(day for day, _ in serie if start <= day <= end)
    return sorted(days)


def _apply(transaction: Transaction, holdings: dict[int, Decimal]) -> Decimal:
    """Aplica una transacción y devuelve el movimiento de caja, en divisa base.

    Reproduce las mismas reglas que `pnl.replay_ledger`, con la diferencia de
    que aquí solo interesan cantidades y caja: el coste medio no hace falta
    para valorar a precio de mercado.

    La APORTACIÓN neta no sale de aquí sino de `_external_flows`, y tiene que
    ser así: las dos líneas de la curva -lo que vale y lo que se puso- deben
    venir de la misma fuente o dirían cosas distintas.
    """
    fx = transaction.fx_rate_to_base
    fees = to_decimal(transaction.fees or 0) * fx
    kind = transaction.type

    if kind is TransactionType.BUY:
        cost = transaction.quantity * transaction.price * fx + fees
        holdings[transaction.asset_id] = (
            holdings.get(transaction.asset_id, ZERO) + transaction.quantity
        )
        return -cost

    if kind is TransactionType.SELL:
        proceeds = transaction.quantity * transaction.price * fx - fees
        holdings[transaction.asset_id] = (
            holdings.get(transaction.asset_id, ZERO) - transaction.quantity
        )
        return proceeds

    if kind is TransactionType.DIVIDEND:
        return transaction.cash_amount * fx - fees

    if kind is TransactionType.DEPOSIT:
        return transaction.cash_amount * fx - fees

    if kind is TransactionType.WITHDRAWAL:
        return -(transaction.cash_amount * fx + fees)

    if kind is TransactionType.FEE:
        return -fees

    return ZERO


def _external_flows(
    transactions: list[Transaction],
) -> tuple[list[tuple[dt.date, Decimal]], bool]:
    """Los movimientos que meten y sacan dinero de la cartera, en divisa base.

    Son la vara de medir: el índice se compara aplicando ESTOS mismos flujos, y
    el XIRR se calcula sobre ellos. Un flujo positivo es dinero que entra a la
    cartera.

    Lo normal es usar depósitos y retiradas. Pero la caja puede quedar negativa
    a propósito -es lo que permite cargar un histórico ya existente sin
    registrar los depósitos previos-, y en ese caso no hay ni un depósito que
    mirar: el índice saldría plano en cero y el XIRR sería indefinido, que es
    peor que una aproximación declarada.

    Cuando no hay ningún movimiento de caja se usan las COMPRAS y VENTAS como
    aportación: el dinero de una compra tuvo que entrar de algún sitio. El
    booleano devuelto dice si se recurrió a esa suposición, para que la
    respuesta pueda declararlo en vez de disimularlo.
    """
    deposits = [
        (t.executed_at.date(), to_decimal(t.cash_amount) * t.fx_rate_to_base
         * (Decimal(1) if t.type is TransactionType.DEPOSIT else Decimal(-1)))
        for t in transactions
        if t.type in (TransactionType.DEPOSIT, TransactionType.WITHDRAWAL)
    ]
    if deposits:
        return deposits, False

    trades = [
        (t.executed_at.date(), t.quantity * t.price * t.fx_rate_to_base
         * (Decimal(1) if t.type is TransactionType.BUY else Decimal(-1)))
        for t in transactions
        if t.type.is_trade
    ]
    return trades, True


def xirr(
    flows: list[tuple[dt.date, Decimal]], final_value: Decimal, final_date: dt.date
) -> float | None:
    """Rentabilidad anualizada ponderada por dinero (tasa interna de retorno).

    POR QUÉ HACE FALTA. El retorno que muestra el tablero es
    `total_pnl / total_invested`: ignora el tiempo por completo, así que +30%
    en seis meses y +30% en cinco años dan exactamente la misma cifra. Y al
    ignorar CUÁNDO entró cada peso, tampoco se puede comparar con un índice.

    Se resuelve por BISECCIÓN y no por Newton-Raphson. Newton es más rápido
    pero diverge con series de flujos irregulares -que es justo lo que produce
    una cartera real- y devolvería un número plausible y falso. La bisección
    converge siempre si hay cambio de signo, y si no lo hay se devuelve None:
    una cartera que nunca recibió dinero no tiene tasa de retorno.

    Devuelve una fracción anual: 0.12 es un 12%.
    """
    movements = [(day, -amount) for day, amount in flows if amount != ZERO]
    if not movements or final_value is None:
        return None
    movements.append((final_date, final_value))

    origin = min(day for day, _ in movements)

    def npv(rate: float) -> float:
        total = 0.0
        for day, amount in movements:
            years = (day - origin).days / 365.0
            # (1+r) puede acercarse a 0 por abajo; se acota para no explotar.
            total += float(amount) / ((1.0 + rate) ** years)
        return total

    low, high = -0.9999, 10.0
    npv_low, npv_high = npv(low), npv(high)
    if npv_low * npv_high > 0:
        # Sin cambio de signo no hay raíz en el intervalo: pasa cuando todos
        # los flujos van en el mismo sentido (nunca se invirtió nada, o nunca
        # se recuperó nada). Inventar una tasa aquí sería inventar un dato.
        return None

    for _ in range(200):
        middle = (low + high) / 2
        value = npv(middle)
        if abs(value) < 1e-7:
            return middle
        if value * npv_low < 0:
            high = middle
        else:
            low, npv_low = middle, value
    return (low + high) / 2


def _benchmark_asset(db: Session, base_currency: str) -> Asset | None:
    """Índice de referencia con serie GUARDADA.

    Se recorre en orden de preferencia y se coge el primero que exista. No se
    sale a la red: un servicio que llama al proveedor deja de ser testeable sin
    red y mete latencia en una lectura.
    """
    found = market_repo.get_assets_by_symbols(db, list(BENCHMARK_CANDIDATES))
    for symbol in BENCHMARK_CANDIDATES:
        asset = found.get(symbol)
        if asset is not None:
            return asset
    return None


def compute_series(
    db: Session, portfolio: Portfolio, *, days: int = 365
) -> PerformanceSeries:
    """Valor de la cartera día a día, con el índice y el XIRR.

    Reproduce el ledger a lo largo del calendario de cotización en vez de una
    sola vez. Todo se deriva: no hay ninguna tabla de valores históricos que
    pudiera desincronizarse del ledger.

    El precio usado es `close` y NO `adj_close`. Es deliberado y tiene dos
    motivos: `adj_close` reescribe el pasado también por dividendos, y esos ya
    están en el ledger como entradas de caja -contarlos dos veces inflaría la
    curva-; y el último punto tiene que coincidir con el valor que el tablero
    enseña arriba, que se calcula con el precio real.
    """
    result = PerformanceSeries()
    transactions = portfolio_repo.get_transactions(db, portfolio.id)
    if not transactions:
        result.warnings.append(
            "La cartera no tiene transacciones: no hay nada que dibujar todavía."
        )
        return result

    base = portfolio.base_currency.upper()
    today = dt.date.today()
    first_day = min(t.executed_at.date() for t in transactions)
    start = max(first_day, today - dt.timedelta(days=days))
    # Se piden datos desde ANTES del inicio para que el relleno hacia adelante
    # tenga de dónde arrastrar el primer día: si el 1 de enero no hay barra, el
    # valor correcto es el del 31 de diciembre, no un hueco.
    lookback = start - dt.timedelta(days=30)

    asset_ids = sorted({t.asset_id for t in transactions if t.asset_id})
    assets = {
        a.id: a for a in db.query(Asset).filter(Asset.id.in_(asset_ids)).all()
    } if asset_ids else {}

    prices = market_repo.get_price_series_dated(
        db, asset_ids, since=lookback, adjusted=False
    )
    currencies = {a.currency.upper() for a in assets.values()} - {base}

    benchmark = _benchmark_asset(db, base)
    benchmark_prices: list[tuple[dt.date, float]] = []
    if benchmark is not None:
        benchmark_prices = market_repo.get_price_series_dated(
            db, [benchmark.id], since=lookback, adjusted=False
        ).get(benchmark.id, [])
        if benchmark_prices:
            result.benchmark_symbol = benchmark.symbol
            currencies |= {benchmark.currency.upper()} - {base}
        else:
            result.warnings.append(
                f"Sin histórico de {benchmark.symbol}: no hay línea de referencia."
            )
    else:
        result.warnings.append(
            "Ningún índice de referencia en el catálogo (SPY o equivalente): "
            "no hay línea contra la que comparar."
        )

    fx_series = market_repo.get_fx_series(
        db, [(c, base) for c in currencies], since=lookback
    )
    fx_readers = {c: _Forward(fx_series.get((c, base), [])) for c in currencies}
    price_readers = {aid: _Forward(prices.get(aid, [])) for aid in asset_ids}
    benchmark_reader = _Forward(benchmark_prices)

    calendar = _trading_calendar(prices, start, today)
    if not calendar:
        result.warnings.append(
            "No hay barras de precio en el rango pedido: ejecuta una "
            "sincronización para poblar el histórico."
        )
        return result

    flows, assumed = _external_flows(transactions)
    if assumed:
        result.warnings.append(
            "La cartera no registra depósitos, así que las compras y ventas se "
            "toman como aportación para la comparación y el XIRR."
        )
    flows_by_day: dict[dt.date, Decimal] = {}
    for day, amount in flows:
        flows_by_day[day] = flows_by_day.get(day, ZERO) + amount

    def rate_at(currency: str, day: dt.date) -> Decimal | None:
        if currency == base:
            return Decimal(1)
        reader = fx_readers.get(currency)
        return None if reader is None else reader.at(day)

    holdings: dict[int, Decimal] = {}
    cash = ZERO
    net_invested = ZERO
    benchmark_units = Decimal(0)
    index = 0
    incomplete: set[str] = set()

    for day in calendar:
        while index < len(transactions) and transactions[index].executed_at.date() <= day:
            cash += _apply(transactions[index], holdings)
            index += 1

        # --- El índice recibe la misma aportación el mismo día ---
        contribution = flows_by_day.pop(day, None)
        if contribution:
            net_invested += contribution
            if assumed:
                # Sin depósitos registrados, la compra dejaría la caja negativa
                # por su importe entero y `total_value` acabaría mostrando solo
                # la plusvalía: en la cartera de prueba, 88.870 COP en vez de
                # 2,2 millones. Se acredita la aportación implícita el mismo
                # día, que es lo que de verdad pasó: el dinero entró para
                # comprar.
                cash += contribution

        benchmark_price = benchmark_reader.at(day)
        if benchmark is not None and benchmark_price and contribution:
            rate = rate_at(benchmark.currency.upper(), day)
            if rate:
                unit_cost = to_decimal(benchmark_price) * rate
                if unit_cost > ZERO:
                    benchmark_units += contribution / unit_cost

        # --- Valoración de la cartera ---
        market_value = ZERO
        usable = True
        for asset_id, quantity in holdings.items():
            if quantity <= ZERO:
                continue
            asset = assets.get(asset_id)
            price = price_readers[asset_id].at(day)
            rate = rate_at(asset.currency.upper(), day) if asset else None
            if price is None or rate is None or asset is None:
                usable = False
                incomplete.add(asset.symbol if asset else str(asset_id))
                continue
            market_value += quantity * to_decimal(price) * rate

        if not usable:
            # Un punto con una posición sin valorar NO se emite: dibujarlo
            # mostraría una caída que solo existe en los datos. Es la misma
            # regla que en el resto del sistema, donde un precio ausente es
            # None y nunca 0.
            continue

        benchmark_value: Decimal | None = None
        if benchmark_units > ZERO and benchmark_price:
            rate = rate_at(benchmark.currency.upper(), day)
            if rate:
                benchmark_value = benchmark_units * to_decimal(benchmark_price) * rate

        result.points.append(
            ValuePoint(
                date=day,
                market_value=market_value,
                cash=cash,
                total_value=market_value + cash,
                net_invested=net_invested,
                benchmark_value=benchmark_value,
            )
        )

    if incomplete:
        names = ", ".join(sorted(incomplete)[:4])
        result.warnings.append(
            f"Días sin dibujar por falta de precio o tipo de cambio en: {names}"
            + (" y otros" if len(incomplete) > 4 else "")
        )

    if result.points:
        last = result.points[-1]
        span = (last.date - result.points[0].date).days
        if span >= XIRR_MIN_DAYS:
            result.xirr = xirr(flows, last.total_value, last.date)
            if last.benchmark_value is not None:
                result.benchmark_xirr = xirr(flows, last.benchmark_value, last.date)
        else:
            result.warnings.append(
                f"Solo {span} días de historia: no se anualiza el retorno todavía "
                f"(hacen falta {XIRR_MIN_DAYS}). Anualizar un tramo corto "
                f"multiplica el ruido."
            )

    return result
