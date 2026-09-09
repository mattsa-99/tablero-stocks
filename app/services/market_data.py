"""Servicio de datos de mercado: orquesta proveedor, caché y persistencia.

Frontera de responsabilidades:
- `providers/yfinance_client.py` habla con Yahoo y no sabe nada de la BD.
- `providers/cache.py` decide CUÁNDO ir a la red (TTL + backoff).
- Este módulo decide QUÉ pedir, y persiste el resultado.
- El resto de servicios NUNCA llama al proveedor: leen de las tablas.

Comportamiento ante fallo: DEGRADACIÓN CON AVISOS. Si el fetch falla se
conserva el último dato conocido, se marca `is_stale` y se acumula un aviso.
Un fallo de Yahoo no puede dejar el dashboard en blanco.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import ProviderError, SymbolNotFound
from app.db.bulk import insert_ignore_duplicates
from app.models import Asset, AssetQuote, AssetType, FundamentalSnapshot, FxRateDaily, PriceHistory
from app.providers.base import MarketProvider
from app.providers.cache import ResourceType, SyncGate, flight_lock
from app.repositories import market as market_repo
from app.services import data_quality

logger = logging.getLogger(__name__)

# Rangos de plausibilidad para detectar un par de divisas invertido.
# Un USD/COP leído al revés vale ~0.00025 en lugar de ~4000: un error de siete
# órdenes de magnitud que hay que hacer fallar de forma ruidosa, no absorber.
#
# Estos son OVERRIDES explícitos, para los pares dominantes y para aquellos
# cercanos a la paridad, donde la comprobación derivada de abajo no sirve.
FX_SANITY_RANGES: dict[tuple[str, str], tuple[float, float]] = {
    ("USD", "COP"): (1_000.0, 15_000.0),
    ("EUR", "COP"): (1_000.0, 18_000.0),
    ("USD", "EUR"): (0.5, 2.0),
    ("EUR", "USD"): (0.5, 2.0),
    ("USD", "CHF"): (0.5, 2.0),
    ("CHF", "USD"): (0.5, 2.0),
    ("GBP", "USD"): (0.5, 2.5),
    ("USD", "GBP"): (0.4, 2.0),
}

# ORDEN DE MAGNITUD de cada divisa en USD. NO son tipos de cambio: no se usan
# jamás para convertir, solo para saber si un número tiene el tamaño correcto.
# Una tabla por par no escala -25 divisas son 600 pares posibles-, mientras que
# un número por divisa deriva la banda de cualquier par: se espera que
# base/quote valga aproximadamente magnitud[base] / magnitud[quote].
#
# Sirven durante años sin tocarlos porque la tolerancia es de un factor 4,
# mientras que una inversión yerra por un factor r^2 (para USD/COP, 10^7).
CURRENCY_USD_MAGNITUDE: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.16, "GBP": 1.35, "CHF": 1.24, "DKK": 0.155,
    "SEK": 0.104, "NOK": 0.107, "PLN": 0.268,
    "JPY": 0.0063, "HKD": 0.128, "CNY": 0.149, "KRW": 0.0007,
    "TWD": 0.032, "SGD": 0.786, "INR": 0.0105,
    "AUD": 0.717, "CAD": 0.721, "NZD": 0.60,
    "COP": 0.00031, "BRL": 0.193, "MXN": 0.059, "CLP": 0.0011,
    "PEN": 0.298, "ARS": 0.0007,
    "ZAR": 0.062, "ILS": 0.335, "TRY": 0.021,
}

# Cuánto puede alejarse un tipo real de su magnitud esperada sin considerarse
# sospechoso. Cubre años de deriva: una inversión yerra muchísimo más.
_MAGNITUDE_TOLERANCE = 4.0

# Umbral por debajo del cual el par NO se puede comprobar por magnitud.
#
# No es un número elegido a ojo, es el punto exacto donde el método deja de
# funcionar: si el tipo esperado es `e`, el invertido es `1/e`, y su distancia
# relativa es e^2. La inversión solo cae FUERA de la banda cuando
# e^2 > tolerancia, es decir e > sqrt(tolerancia). Por debajo de eso el valor
# invertido cae dentro de la banda del correcto y comprobar sería teatro.
#
# Esos pares (EUR/USD, GBP/USD...) necesitan un override explícito en
# FX_SANITY_RANGES o se quedan sin comprobar, que es preferible a un falso
# positivo: rechazar un tipo correcto deja la cartera entera sin valorar.
_UNCHECKABLE_NEAR_PARITY = _MAGNITUDE_TOLERANCE**0.5


def _derived_bounds(base: str, quote: str) -> tuple[float, float] | None:
    """Banda plausible deducida de la magnitud de ambas divisas."""
    base_mag = CURRENCY_USD_MAGNITUDE.get(base)
    quote_mag = CURRENCY_USD_MAGNITUDE.get(quote)
    if not base_mag or not quote_mag:
        return None
    expected = base_mag / quote_mag
    if 1 / _UNCHECKABLE_NEAR_PARITY <= expected <= _UNCHECKABLE_NEAR_PARITY:
        return None
    return expected / _MAGNITUDE_TOLERANCE, expected * _MAGNITUDE_TOLERANCE

_ASSET_TYPE_MAP = {
    "EQUITY": AssetType.STOCK,
    "ETF": AssetType.ETF,
    "MUTUALFUND": AssetType.FUND,
    "CRYPTOCURRENCY": AssetType.CRYPTO,
}


@dataclass
class RefreshReport:
    """Qué se actualizó y qué falló. Se propaga a la respuesta de la API."""

    quotes_updated: int = 0
    bars_written: int = 0
    fundamentals_updated: int = 0
    fx_updated: int = 0
    metadata_updated: int = 0
    warnings: list[str] = field(default_factory=list)
    failed_symbols: list[str] = field(default_factory=list)

    def merge(self, other: RefreshReport) -> RefreshReport:
        self.quotes_updated += other.quotes_updated
        self.bars_written += other.bars_written
        self.fundamentals_updated += other.fundamentals_updated
        self.fx_updated += other.fx_updated
        self.metadata_updated += other.metadata_updated
        self.warnings.extend(other.warnings)
        self.failed_symbols.extend(other.failed_symbols)
        return self


def fx_sanity_check(base: str, quote: str, rate: float) -> str | None:
    """Devuelve un aviso si el tipo de cambio parece invertido o absurdo."""
    if rate <= 0:
        return f"Tipo de cambio {base}/{quote} no positivo: {rate}"
    base, quote = base.upper(), quote.upper()
    bounds = FX_SANITY_RANGES.get((base, quote)) or _derived_bounds(base, quote)
    if bounds is None:
        return None
    low, high = bounds
    if not (low <= rate <= high):
        return (
            f"Tipo de cambio {base}/{quote} = {rate:g} fuera del rango plausible "
            f"[{low:g}, {high:g}]. Posible par invertido: NO se ha guardado."
        )
    return None


class MarketDataService:
    def __init__(self, db: Session, provider: MarketProvider) -> None:
        self.db = db
        self.provider = provider
        self.gate = SyncGate(db)

    # ------------------------------------------------------------------
    # Cotizaciones
    # ------------------------------------------------------------------

    def refresh_quotes(self, assets: list[Asset], *, force: bool = False) -> RefreshReport:
        """Refresca las cotizaciones caducadas de los activos dados."""
        report = RefreshReport()
        ttl = self._quote_ttl()

        stale = [
            a
            for a in assets
            if self.gate.should_fetch(ResourceType.QUOTE, a.symbol, ttl, force=force)
        ]
        if not stale:
            return report

        by_symbol = {a.symbol: a for a in stale}
        symbols = sorted(by_symbol)
        for symbol in symbols:
            self.gate.mark_attempt(ResourceType.QUOTE, symbol, ttl)

        try:
            quotes = self.provider.fetch_quotes(symbols)
        except ProviderError as exc:
            for symbol in symbols:
                self.gate.mark_failure(ResourceType.QUOTE, symbol, ttl, exc)
            self._mark_quotes_stale(list(by_symbol.values()))
            report.warnings.append(f"No se pudieron actualizar cotizaciones: {exc}")
            report.failed_symbols.extend(symbols)
            self.db.commit()
            return report

        for symbol, asset in by_symbol.items():
            data = quotes.get(symbol)
            if data is None:
                self.gate.mark_failure(
                    ResourceType.QUOTE, symbol, ttl, SymbolNotFound(symbol)
                )
                self._mark_quotes_stale([asset])
                report.warnings.append(f"Sin cotización para {symbol}")
                report.failed_symbols.append(symbol)
                continue

            quote = self.db.get(AssetQuote, asset.id)
            if quote is None:
                quote = AssetQuote(asset_id=asset.id, price=data.price, currency=data.currency)
                self.db.add(quote)
            quote.price = data.price
            quote.previous_close = data.previous_close
            quote.currency = data.currency or asset.currency
            quote.quote_time = data.quote_time
            quote.fetched_at = dt.datetime.now(dt.UTC)
            quote.is_stale = False

            self.gate.mark_success(ResourceType.QUOTE, symbol, ttl, payload=data.price)
            report.quotes_updated += 1

        self.db.commit()
        return report

    def _mark_quotes_stale(self, assets: list[Asset]) -> None:
        for asset in assets:
            quote = self.db.get(AssetQuote, asset.id)
            if quote is not None:
                quote.is_stale = True

    @staticmethod
    def _quote_ttl() -> int:
        """TTL corto en horario bursátil, largo fuera de él.

        Simplificación consciente: una única ventana 13:30-21:00 UTC (NYSE) sin
        calendario de festivos. El coste de equivocarse es una llamada de más,
        nunca un dato incorrecto. Afinarlo por bolsa exige `asset.exchange` y
        un calendario, que no compensa en esta fase.
        """
        now = dt.datetime.now(dt.UTC)
        is_weekday = now.weekday() < 5
        in_session = dt.time(13, 30) <= now.time() <= dt.time(21, 0)
        if is_weekday and in_session:
            return settings.quote_ttl_seconds
        return settings.quote_ttl_market_closed_seconds

    @staticmethod
    def _fx_ttl() -> int:
        """TTL de divisas: corto entre semana, largo el fin de semana.

        NO reutiliza la ventana de `_quote_ttl()`. El mercado de divisas opera
        de forma continua de domingo por la tarde a viernes por la tarde (ET),
        no en la sesión 13:30-21:00 UTC de la NYSE: aplicarle el horario de una
        bolsa dejaría el USD/COP congelado durante horas en las que sí se mueve.

        Lo que de verdad para el mercado es el fin de semana, y ahí sí conviene
        el TTL largo: pedir un tipo que no puede haber cambiado solo gasta
        presupuesto de rate limit.
        """
        now = dt.datetime.now(dt.UTC)
        return (
            settings.fx_ttl_seconds
            if now.weekday() < 5
            else settings.fx_ttl_market_closed_seconds
        )

    # ------------------------------------------------------------------
    # Histórico de precios
    # ------------------------------------------------------------------

    def refresh_price_history(
        self, assets: list[Asset], *, force: bool = False
    ) -> RefreshReport:
        """Descarga solo las barras AUSENTES y las inserta en bloque.

        Las barras cerradas son inmutables, así que se pide únicamente desde el
        día siguiente a la última almacenada. Es la mayor economía de llamadas
        de todo el sistema: sin esto, cada refresco redescargaría 400 días.

        Dos optimizaciones respecto de la versión inicial, ambas necesarias al
        crecer el universo:

        1. Las últimas fechas se resuelven en UNA consulta agregada, no una por
           activo (y sin cargar 400 objetos ORM por activo solo para leerles la
           fecha).
        2. La inserción es `INSERT ... ON CONFLICT DO NOTHING` en bloque con el
           Core, no `db.add()` por barra. Un backfill de 26 activos x 400 barras
           son ~10.000 filas: con el ORM eso es instanciar y rastrear 10.000
           objetos para escribirlos una sola vez.

        El ON CONFLICT también hace innecesaria la comprobación previa de
        existencia: la PK (asset_id, date) ya garantiza la unicidad, y dejar que
        la base decida elimina una carrera entre la comprobación y la escritura.
        """
        report = RefreshReport()
        ttl = settings.price_history_ttl_seconds
        today = dt.date.today()

        candidates = [
            asset
            for asset in assets
            if self.gate.should_fetch(
                ResourceType.PRICE_HISTORY, asset.symbol, ttl, force=force
            )
        ]
        if not candidates:
            return report

        last_dates = market_repo.get_last_bar_dates(
            self.db, [asset.id for asset in candidates]
        )
        default_start = today - dt.timedelta(days=settings.price_history_days)

        pending: dict[str, tuple[Asset, dt.date]] = {}
        for asset in candidates:
            last = last_dates.get(asset.id)
            start = last + dt.timedelta(days=1) if last else default_start
            if start > today:
                continue  # ya está al día: ni siquiera se pide
            pending[asset.symbol] = (asset, start)

        if not pending:
            return report

        symbols = sorted(pending)
        window_start = min(start for _, start in pending.values())
        for symbol in symbols:
            self.gate.mark_attempt(ResourceType.PRICE_HISTORY, symbol, ttl)

        try:
            history = self.provider.fetch_history(symbols, window_start, today)
        except ProviderError as exc:
            for symbol in symbols:
                self.gate.mark_failure(ResourceType.PRICE_HISTORY, symbol, ttl, exc)
            report.warnings.append(f"No se pudo actualizar el histórico: {exc}")
            report.failed_symbols.extend(symbols)
            self.db.commit()
            return report

        payload: list[dict] = []
        for symbol, (asset, asset_start) in pending.items():
            bars = history.get(symbol)
            if not bars:
                self.gate.mark_failure(
                    ResourceType.PRICE_HISTORY, symbol, ttl, SymbolNotFound(symbol)
                )
                report.warnings.append(f"Sin histórico para {symbol}")
                continue

            payload.extend(
                {
                    "asset_id": asset.id,
                    "date": bar.date,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "adj_close": bar.adj_close,
                    "volume": bar.volume,
                }
                for bar in bars
                if bar.date >= asset_start
            )
            self.gate.mark_success(ResourceType.PRICE_HISTORY, symbol, ttl)

        if payload:
            report.bars_written += insert_ignore_duplicates(
                self.db,
                PriceHistory.__table__,
                payload,
                index_elements=["asset_id", "date"],
            )

        self.db.commit()
        return report

    # ------------------------------------------------------------------
    # Fundamentales y metadatos
    # ------------------------------------------------------------------

    def refresh_fundamentals(
        self, assets: list[Asset], *, force: bool = False
    ) -> RefreshReport:
        report = RefreshReport()
        ttl = settings.fundamentals_ttl_seconds
        today = dt.date.today()

        pending = {
            a.symbol: a
            for a in assets
            if self.gate.should_fetch(ResourceType.FUNDAMENTALS, a.symbol, ttl, force=force)
        }
        if not pending:
            return report

        symbols = sorted(pending)
        for symbol in symbols:
            self.gate.mark_attempt(ResourceType.FUNDAMENTALS, symbol, ttl)

        try:
            data = self.provider.fetch_fundamentals(symbols)
        except ProviderError as exc:
            for symbol in symbols:
                self.gate.mark_failure(ResourceType.FUNDAMENTALS, symbol, ttl, exc)
            report.warnings.append(f"No se pudieron actualizar fundamentales: {exc}")
            report.failed_symbols.extend(symbols)
            self.db.commit()
            return report

        for symbol, asset in pending.items():
            fundamental = data.get(symbol)
            if fundamental is None:
                self.gate.mark_failure(
                    ResourceType.FUNDAMENTALS, symbol, ttl, SymbolNotFound(symbol)
                )
                report.warnings.append(f"Sin fundamentales para {symbol}")
                continue

            # Un snapshot por activo y día: la UNIQUE (asset_id, as_of) hace
            # idempotente refrescar varias veces el mismo día.
            #
            # Se consulta la BD en vez de recorrer `asset.fundamentals`: esa
            # relación puede haberse cargado antes del insert de este mismo
            # ciclo, y entonces no vería el snapshot recién creado -> IntegrityError.
            existing = self.db.scalar(
                select(FundamentalSnapshot).where(
                    FundamentalSnapshot.asset_id == asset.id,
                    FundamentalSnapshot.as_of == today,
                )
            )
            snapshot = existing or FundamentalSnapshot(asset_id=asset.id, as_of=today)
            snapshot.market_cap = (
                Decimal(str(fundamental.market_cap))
                if fundamental.market_cap is not None
                else None
            )
            # FILTRO DE DATOS ROTOS, antes de escribir.
            #
            # Los ratios que dividen el precio entre una magnitud contable por
            # acción llegan sin convertir la divisa cuando la empresa cotiza
            # en una y reporta en otra. Se descartan aquí, en la frontera, para
            # que aguas abajo nadie tenga que preguntarse si el número es real.
            # Ver `services/data_quality.py` para el porqué y las mediciones.
            checked = data_quality.sanitise_price_ratios(
                {
                    name: getattr(fundamental, name)
                    for name in data_quality.PRICE_TO_BOOK_RATIOS
                },
                quote_currency=asset.currency,
                financial_currency=fundamental.financial_currency,
            )
            if checked.has_drops:
                detail = "; ".join(
                    f"{ratio} ({motivo})" for ratio, motivo in checked.dropped.items()
                )
                report.warnings.append(f"{symbol}: se descartó {detail}")
                logger.warning("%s: ratios descartados -> %s", symbol, detail)

            for attribute in (
                "trailing_pe",
                "forward_pe",
                "price_to_book",
                "price_to_sales",
                "ev_to_ebitda",
                "profit_margin",
                "return_on_equity",
                "debt_to_equity",
                "revenue_growth",
                "earnings_growth",
                "eps_trailing",
                "dividend_yield",
                "payout_ratio",
                "beta",
                "fifty_two_week_high",
                "fifty_two_week_low",
            ):
                # `checked` manda sobre lo que vino del proveedor: si el
                # ratio no pasó el filtro, se guarda ausente y no un valor
                # equivocado que parecería medido.
                value = (
                    checked.values[attribute]
                    if attribute in checked.values
                    else getattr(fundamental, attribute)
                )
                setattr(snapshot, attribute, value)
            snapshot.raw = fundamental.raw
            snapshot.fetched_at = dt.datetime.now(dt.UTC)
            if existing is None:
                self.db.add(snapshot)

            self.gate.mark_success(ResourceType.FUNDAMENTALS, symbol, ttl)
            report.fundamentals_updated += 1

        self.db.commit()
        return report

    def refresh_metadata(self, assets: list[Asset], *, force: bool = False) -> RefreshReport:
        report = RefreshReport()
        ttl = settings.metadata_ttl_seconds

        pending = {
            a.symbol: a
            for a in assets
            if self.gate.should_fetch(ResourceType.METADATA, a.symbol, ttl, force=force)
        }
        if not pending:
            return report

        symbols = sorted(pending)
        for symbol in symbols:
            self.gate.mark_attempt(ResourceType.METADATA, symbol, ttl)

        try:
            metadata = self.provider.fetch_metadata(symbols)
        except ProviderError as exc:
            for symbol in symbols:
                self.gate.mark_failure(ResourceType.METADATA, symbol, ttl, exc)
            report.warnings.append(f"No se pudieron actualizar metadatos: {exc}")
            self.db.commit()
            return report

        for symbol, asset in pending.items():
            info = metadata.get(symbol)
            if info is None:
                # El ticker no se pudo verificar. Se desactiva para dejar de
                # gastar llamadas, pero NO se borra: puede tener transacciones.
                asset.is_active = False
                self.gate.mark_failure(
                    ResourceType.METADATA, symbol, ttl, SymbolNotFound(symbol)
                )
                report.warnings.append(f"Ticker no verificable en el proveedor: {symbol}")
                report.failed_symbols.append(symbol)
                continue

            asset.name = info.name or asset.name
            asset.currency = info.currency or asset.currency
            asset.exchange = info.exchange or asset.exchange
            asset.sector = info.sector or asset.sector
            asset.industry = info.industry or asset.industry
            asset.country = info.country or asset.country
            if info.asset_type in _ASSET_TYPE_MAP:
                asset.asset_type = _ASSET_TYPE_MAP[info.asset_type]
            asset.is_active = True
            asset.last_verified_at = dt.datetime.now(dt.UTC)

            self.gate.mark_success(ResourceType.METADATA, symbol, ttl)
            report.metadata_updated += 1

        self.db.commit()
        return report

    # ------------------------------------------------------------------
    # Divisas
    # ------------------------------------------------------------------

    def refresh_fx(
        self, pairs: list[tuple[str, str]], *, force: bool = False
    ) -> RefreshReport:
        report = RefreshReport()
        ttl = self._fx_ttl()
        today = dt.date.today()

        pending = [
            (base, quote)
            for base, quote in pairs
            if base != quote
            and self.gate.should_fetch(
                ResourceType.FX, f"{base}{quote}", ttl, force=force
            )
        ]
        if not pending:
            return report

        for base, quote in pending:
            self.gate.mark_attempt(ResourceType.FX, f"{base}{quote}", ttl)

        try:
            rates = self.provider.fetch_fx_rates(pending)
        except ProviderError as exc:
            for base, quote in pending:
                self.gate.mark_failure(ResourceType.FX, f"{base}{quote}", ttl, exc)
            report.warnings.append(f"No se pudieron actualizar tipos de cambio: {exc}")
            self.db.commit()
            return report

        unresolved: list[tuple[str, str]] = []
        resolved: dict[tuple[str, str], float] = {}
        for base, quote in pending:
            key = f"{base}{quote}"
            rate = rates.get((base, quote))
            if rate is None:
                # Todavía NO es un fallo: puede que el par no exista en Yahoo
                # pero sí sus dos tramos contra USD. Se decide tras triangular.
                unresolved.append((base, quote))
                continue

            problem = fx_sanity_check(base, quote, rate)
            if problem is not None:
                # Se rechaza el dato ANTES de escribirlo. Un FX invertido
                # corrompe el valor de toda la cartera, no de una posición.
                self.gate.mark_failure(ResourceType.FX, key, ttl, ValueError(problem))
                report.warnings.append(problem)
                logger.error(problem)
                continue

            self._store_fx(base, quote, rate, today)
            resolved[(base, quote)] = rate
            self.gate.mark_success(ResourceType.FX, key, ttl, payload=rate)
            report.fx_updated += 1

        if unresolved:
            self._triangulate_fx(unresolved, report, ttl, today, resolved)

        self.db.commit()
        return report

    # ------------------------------------------------------------------

    def _store_fx(
        self, base: str, quote: str, rate: float, today: dt.date, *, source: str = "yfinance"
    ) -> None:
        """Escribe (o pisa) el tipo del día. `source` deja constancia del origen."""
        existing = self.db.get(FxRateDaily, (base, quote, today))
        if existing is None:
            self.db.add(
                FxRateDaily(
                    base_currency=base,
                    quote_currency=quote,
                    date=today,
                    rate=Decimal(str(rate)),
                    source=source,
                    fetched_at=dt.datetime.now(dt.UTC),
                )
            )
        else:
            existing.rate = Decimal(str(rate))
            existing.source = source
            existing.fetched_at = dt.datetime.now(dt.UTC)

    def _leg_rate(
        self,
        base: str,
        quote: str,
        today: dt.date,
        known: dict[tuple[str, str], float],
    ) -> float | None:
        """Un tramo de la triangulación, del día.

        Mira primero `known`, que son los tipos resueltos en esta misma llamada.
        La sesión va con `autoflush=False`, así que un tipo recién añadido con
        `db.add()` NO es visible para `db.get()` hasta el commit: leerlo de la
        BD daría None y la triangulación fallaría teniendo el dato en la mano.
        Se evita a propósito forzar un flush intermedio, que abriría el write
        lock de SQLite antes de tiempo.
        """
        if base == quote:
            return 1.0
        cached = known.get((base, quote))
        if cached is not None:
            return cached
        stored = self.db.get(FxRateDaily, (base, quote, today))
        if stored is not None:
            rate = float(stored.rate)
            known[(base, quote)] = rate
            return rate
        return None

    def _triangulate_fx(
        self,
        pairs: list[tuple[str, str]],
        report: RefreshReport,
        ttl: int,
        today: dt.date,
        known: dict[tuple[str, str], float],
    ) -> None:
        """Deriva los pares sin cotización directa pasando por USD.

        La mayoría de divisas NO tiene par contra el peso colombiano: HKDCOP=X,
        KRWCOP=X o MXNCOP=X devuelven 404. Todas, en cambio, cotizan contra el
        dólar. Con USD de pivote, HKD/COP = HKD/USD x USD/COP.

        Esto NO es asumir un dato que falta -eso está prohibido y por eso una
        operación sin tipo de cambio se rechaza-: son dos tipos reales medidos,
        compuestos. Se marcan con `source` para que la procedencia sea legible,
        y pasan la MISMA comprobación de sanidad que un tipo directo: componer
        dos tramos también puede producir un disparate si uno viene invertido.
        """
        pivot = "USD"
        # Con el pivote en un extremo no hay nada que triangular: si USD/COP
        # falla, derivarlo de USD/USD x USD/COP sería circular.
        candidates = [(b, q) for b, q in pairs if pivot not in (b, q)]
        for base, quote in pairs:
            if (base, quote) not in candidates:
                key = f"{base}{quote}"
                self.gate.mark_failure(
                    ResourceType.FX, key, ttl, SymbolNotFound(f"{base}/{quote}")
                )
                report.warnings.append(f"Sin tipo de cambio para {base}/{quote}")
        if not candidates:
            return

        # Se piden solo los tramos que no estén ya guardados hoy.
        needed: set[tuple[str, str]] = set()
        for base, quote in candidates:
            if self._leg_rate(base, pivot, today, known) is None:
                needed.add((base, pivot))
            if self._leg_rate(pivot, quote, today, known) is None:
                needed.add((pivot, quote))

        fetched: dict[tuple[str, str], float] = {}
        if needed:
            try:
                fetched = self.provider.fetch_fx_rates(sorted(needed))
            except ProviderError as exc:
                report.warnings.append(
                    f"No se pudieron obtener los tramos de triangulación: {exc}"
                )

        for leg, rate in fetched.items():
            problem = fx_sanity_check(leg[0], leg[1], rate)
            if problem is None:
                self._store_fx(leg[0], leg[1], rate, today)
                known[leg] = rate
            else:
                report.warnings.append(problem)
                logger.error(problem)

        for base, quote in candidates:
            key = f"{base}{quote}"
            first = self._leg_rate(base, pivot, today, known)
            second = self._leg_rate(pivot, quote, today, known)
            if first is None or second is None:
                self.gate.mark_failure(
                    ResourceType.FX, key, ttl, SymbolNotFound(f"{base}/{quote}")
                )
                report.warnings.append(
                    f"Sin tipo de cambio para {base}/{quote}: no hay par directo "
                    f"y falta algún tramo contra {pivot}"
                )
                continue

            rate = first * second
            problem = fx_sanity_check(base, quote, rate)
            if problem is not None:
                self.gate.mark_failure(ResourceType.FX, key, ttl, ValueError(problem))
                report.warnings.append(problem)
                logger.error(problem)
                continue

            self._store_fx(base, quote, rate, today, source=f"yfinance:{pivot}")
            self.gate.mark_success(ResourceType.FX, key, ttl, payload=rate)
            report.fx_updated += 1
            logger.info(
                "%s/%s triangulado vía %s: %g x %g = %g",
                base, quote, pivot, first, second, rate,
            )

    # ------------------------------------------------------------------
    # Orquestación
    # ------------------------------------------------------------------

    def ensure_fresh_for_portfolio(
        self, assets: list[Asset], base_currency: str, *, force: bool = False
    ) -> RefreshReport:
        """Refresco perezoso al leer un portafolio: cotizaciones y FX.

        Deliberadamente NO refresca histórico ni fundamentales: eso pertenece
        al job completo. Cargar el dashboard no debe disparar decenas de
        llamadas pesadas.
        """
        report = RefreshReport()
        if not assets:
            return report

        lock = flight_lock("portfolio_refresh", base_currency)
        if not lock.acquire(blocking=False):
            # Otra petición ya está refrescando: se sirve lo que hay en BD.
            report.warnings.append("Refresco en curso; se sirven los últimos datos")
            return report
        try:
            # Metadatos SOLO de los activos nunca verificados. Sin esto, un
            # símbolo recién comprado se queda sin sector hasta la siguiente
            # sincronización diaria, y mientras tanto el gráfico por sector y
            # el factor de diversificación lo agrupan en «Desconocido».
            # Es coste único por símbolo: después el TTL de 30 días lo cubre.
            unverified = [a for a in assets if a.last_verified_at is None]
            if unverified:
                report.merge(self.refresh_metadata(unverified))

            report.merge(self.refresh_quotes(assets, force=force))
            pairs = sorted(
                {(a.currency.upper(), base_currency.upper()) for a in assets}
                - {(base_currency.upper(), base_currency.upper())}
            )
            report.merge(self.refresh_fx(pairs, force=force))
        finally:
            lock.release()
        return report

    def refresh_quotes_and_fx(
        self, assets: list[Asset], base_currencies: list[str], *, force: bool = False
    ) -> RefreshReport:
        """Refresco de jornada: SOLO cotizaciones y tipos de cambio.

        Es el subconjunto de `full_refresh` que cambia dentro del día. Deja
        fuera a propósito barras diarias (los cierres son inmutables),
        fundamentales y metadatos: esos pasan por `.info`, el recurso caro
        (~0,76 s/símbolo), y no tiene sentido pedirlos cuatro veces al día.
        """
        report = RefreshReport()
        if not assets:
            return report

        report.merge(self.refresh_quotes(assets, force=force))
        pairs = sorted(
            {
                (a.currency.upper(), base.upper())
                for a in assets
                for base in base_currencies
                if a.currency.upper() != base.upper()
            }
        )
        report.merge(self.refresh_fx(pairs, force=force))
        return report

    def full_refresh(
        self, assets: list[Asset], base_currencies: list[str], *, force: bool = False
    ) -> RefreshReport:
        """Job completo. Cadencia objetivo: dos veces por semana."""
        report = RefreshReport()
        if not assets:
            return report

        report.merge(self.refresh_metadata(assets, force=force))
        report.merge(self.refresh_quotes(assets, force=force))
        report.merge(self.refresh_price_history(assets, force=force))
        report.merge(self.refresh_fundamentals(assets, force=force))

        pairs = sorted(
            {
                (a.currency.upper(), base.upper())
                for a in assets
                for base in base_currencies
                if a.currency.upper() != base.upper()
            }
        )
        report.merge(self.refresh_fx(pairs, force=force))
        return report
