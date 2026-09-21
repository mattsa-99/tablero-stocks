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
from app.core.exceptions import (
    ProviderError,
    ProviderRateLimited,
    ProviderUnreachable,
    SymbolNotFound,
)
from app.db.bulk import insert_ignore_duplicates, upsert
from app.models import (
    Asset,
    AssetQuote,
    AssetType,
    FundamentalSnapshot,
    FundProfile,
    FxRateDaily,
    PriceHistory,
)
from app.providers.base import MarketProvider
from app.providers.cache import ResourceType, SyncGate, flight_lock
from app.repositories import market as market_repo
from app.services import asset_class, data_quality

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

    # Descartes de RATIOS, que son rutina y no incidencias. Se cuentan en vez
    # de listarse uno por uno: ver `summarise_drops`.
    ratio_drops: dict[str, int] = field(default_factory=dict)
    assets_with_drops: set[str] = field(default_factory=set)

    def drop(self, symbol: str, reasons: dict[str, str]) -> None:
        """Registra un descarte de ratio SIN meterlo en los avisos.

        EL PROBLEMA QUE ESTO RESUELVE. Descartar el P/B de un ADR que cotiza
        en dólares y reporta en euros es comportamiento correcto y pasa con
        unos 80 activos en CADA sincronización. Escribir una línea por activo
        llenaba el informe de ~200 mensajes rutinarios, y como
        `MAX_WARNINGS_STORED` corta en 40, los fallos de verdad -«Sin
        cotización para AVB»- se perdían antes de llegar a la pantalla.

        Un informe en el que no se puede encontrar lo que falló no sirve para
        diagnosticar nada, que es justo para lo que existe.
        """
        self.assets_with_drops.add(symbol)
        for motivo in reasons.values():
            # La causa, no el valor concreto: «fuera del rango plausible»
            # agrupa, «-78.58 fuera del rango plausible» no agruparía nada.
            clave = motivo.split(":")[0].strip()
            if "fuera del rango plausible" in clave:
                clave = "magnitud implausible"
            self.ratio_drops[clave] = self.ratio_drops.get(clave, 0) + 1

    def summarise_drops(self) -> str | None:
        """Una línea con todos los descartes, o None si no hubo ninguno."""
        if not self.ratio_drops:
            return None
        causas = sorted(self.ratio_drops.items(), key=lambda kv: -kv[1])
        detalle = "; ".join(f"{motivo} ({n})" for motivo, n in causas[:4])
        return (
            f"Se descartaron ratios en {len(self.assets_with_drops)} activos "
            f"(rutina, no un fallo): {detalle}."
        )

    def note(self, message: str) -> None:
        """Añade un aviso sin repetirlo.

        `full_refresh` encadena cinco refrescos y todos ven el mismo
        enfriamiento del proveedor: sin esto la misma frase llega cinco veces
        a la interfaz, que la muestra como cinco notificaciones distintas.
        """
        if message not in self.warnings:
            self.warnings.append(message)

    def merge(self, other: RefreshReport) -> RefreshReport:
        self.quotes_updated += other.quotes_updated
        self.bars_written += other.bars_written
        self.fundamentals_updated += other.fundamentals_updated
        self.fx_updated += other.fx_updated
        self.metadata_updated += other.metadata_updated
        for warning in other.warnings:
            self.note(warning)
        self.failed_symbols.extend(other.failed_symbols)
        # Los descartes se SUMAN entre lotes: `full_refresh` corre veinte
        # veces por sincronización y el resumen tiene que ser del total.
        self.assets_with_drops |= other.assets_with_drops
        for motivo, n in other.ratio_drops.items():
            self.ratio_drops[motivo] = self.ratio_drops.get(motivo, 0) + n
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
    # Trato con un proveedor que puede cortarnos
    # ------------------------------------------------------------------

    def _cooling_down(self, report: RefreshReport) -> bool:
        """True si hay que abstenerse de llamar al proveedor ahora mismo.

        Respetar el enfriamiento es lo que convierte el 429 en una pausa en
        vez de una espiral: seguir pidiendo mientras Yahoo rechaza solo alarga
        el castigo y gasta minutos de reloj en respuestas que ya sabemos que
        no van a llegar.
        """
        until = self.gate.provider_cooldown_until()
        if until is None:
            return False
        report.note(
            "Yahoo no está respondiendo bien ahora mismo. Se sirven los "
            f"últimos datos guardados; se reintenta a las {until:%H:%M} UTC"
        )
        return True

    def _blame_failure(
        self,
        exc: ProviderError,
        resource_type: str,
        keys: list[str],
        ttl: int,
        context: str,
    ) -> str:
        """Reparte la culpa de un fallo y devuelve el aviso para el usuario.

        La distinción es la que separa degradar de averiarse. Hay dos fallos
        que NO son atribuibles a lo que se pidió, y son justo los que pasan:

        - **Un 429** dice que hay que parar de pedir, no que el ticker esté
          mal. Las sincronizaciones del 16, 17 y 18 de septiembre de 2026
          marcaron 469, 420 y 494 símbolos como fallidos por un límite ajeno.
        - **Una caída de red** dice todavía menos: no se llegó a preguntar.
          Es además el caso dominante -1.282 fallos de DNS contra 23 de rate
          limit en el log del agente-, y el que producía los 25 mensajes
          idénticos por lote que llenaban la terminal.

        Cargárselos a cada símbolo les sube el contador de fallos consecutivos
        y los mete en un backoff exponencial individual, como si el ticker
        estuviera roto. Un universo entero en penitencia por una wifi caída.

        Cualquier otro fallo del proveedor -una respuesta ilegible, un símbolo
        que rompe el lote- sí es atribuible a lo que se pidió, y ahí el
        backoff por clave es exactamente lo que se quiere.
        """
        if isinstance(exc, ProviderRateLimited):
            until = self.gate.mark_rate_limited(exc)
            return (
                f"{context}: Yahoo limitó las peticiones. Se sirven los "
                f"últimos datos guardados; se reintenta a las {until:%H:%M} UTC"
            )
        if isinstance(exc, ProviderUnreachable):
            until = self.gate.mark_unreachable(exc)
            return (
                f"{context}: sin conexión con Yahoo. Se sirven los últimos "
                f"datos guardados; se reintenta a las {until:%H:%M} UTC"
            )
        for key in keys:
            self.gate.mark_failure(resource_type, key, ttl, exc)
        return f"{context}: {exc}"

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

        if self._cooling_down(report):
            return report

        by_symbol = {a.symbol: a for a in stale}
        symbols = sorted(by_symbol)
        for symbol in symbols:
            self.gate.mark_attempt(ResourceType.QUOTE, symbol, ttl)

        try:
            quotes = self.provider.fetch_quotes(symbols)
        except ProviderError as exc:
            self._mark_quotes_stale(list(by_symbol.values()))
            report.note(
                self._blame_failure(
                    exc, ResourceType.QUOTE, symbols, ttl,
                    "No se pudieron actualizar cotizaciones",
                )
            )
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
                quote = AssetQuote(
                    asset_id=asset.id,
                    price=data.price,
                    # `or asset.currency` y no `data.currency` a secas: el
                    # proveedor la deja en None cuando el lote viene de una
                    # descarga agrupada, y la columna es NOT NULL.
                    currency=data.currency or asset.currency,
                )
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
        self, assets: list[Asset], *, force: bool = False, deep: bool = False
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

        HACIA ATRÁS Y HACIA ADELANTE. «Las barras ausentes» son las dos cosas:
        las que faltan al final -lo nuevo- y las que faltan al principio -el
        pasado, cuando la ventana se amplía-. Mirando solo la última barra, lo
        segundo era invisible. Ver `get_bar_date_ranges`.

        `force` salta el TTL diario; `deep` salta además el del relleno hacia
        atrás. Son distintos a propósito: ver el comentario en el bucle.
        """
        report = RefreshReport()
        ttl = settings.price_history_ttl_seconds
        today = dt.date.today()

        ranges = market_repo.get_bar_date_ranges(self.db, [a.id for a in assets])
        depth = settings.price_history_days
        wanted_start = today - dt.timedelta(days=depth)
        backfill_ttl = settings.price_history_backfill_ttl_seconds
        # La PROFUNDIDAD va en la clave del sello, no solo el símbolo. El sello
        # significa «pregunté por el pasado hasta D días y esto es todo lo que
        # hay», y esa respuesta deja de valer en cuanto D cambia. Metiéndola en
        # la clave, ampliar la ventana invalida los sellos por construcción y
        # el relleno corre solo una vez, sin tener que acordarse de forzarlo.
        sello = f"{{}}@{depth}"

        pending: dict[str, tuple[Asset, dt.date]] = {}
        backfilling: set[str] = set()
        for asset in assets:
            stored = ranges.get(asset.id)

            # DOS PREGUNTAS DISTINTAS, cada una con su TTL.
            #
            # "¿Hay barras nuevas?" se contesta mirando la ÚLTIMA almacenada y
            # caduca en horas. "¿Mi serie llega tan atrás como quiero?" se
            # contesta mirando la PRIMERA y caduca en días. Antes solo existía
            # la primera pregunta, y por eso el relleno solo sabía AVANZAR:
            # subir `price_history_days` no traía ni un día más de pasado para
            # ningún activo que ya tuviera barras. Medido sobre la base real:
            # 711 de 711 activos afectados.
            #
            # El TTL diario NO puede bloquear al relleno, o ampliar la ventana
            # no surtiría efecto hasta que caducara algo que no tiene nada que
            # ver.
            #
            # `force` NO llega al relleno, y es deliberado: significa «el TTL
            # diario se me queda corto», no «redescarga cinco años». Si pasara,
            # cada refresco forzado -que es lo que hace el job completo-
            # volvería a pedir el archivo entero de los 494 símbolos. Para eso
            # está `deep`, que es explícito.
            needs_past = stored is None or (
                stored[0] > wanted_start
                and self.gate.should_fetch(
                    ResourceType.PRICE_HISTORY_BACKFILL,
                    sello.format(asset.symbol),
                    backfill_ttl,
                    force=deep,
                )
            )
            needs_new = self.gate.should_fetch(
                ResourceType.PRICE_HISTORY, asset.symbol, ttl, force=force
            )
            if not (needs_past or needs_new):
                continue

            if needs_past:
                start = wanted_start
                backfilling.add(asset.symbol)
            else:
                start = stored[1] + dt.timedelta(days=1)

            if start > today:
                continue  # ya está al día: ni siquiera se pide
            pending[asset.symbol] = (asset, start)

        if not pending:
            return report

        if self._cooling_down(report):
            return report

        symbols = sorted(pending)
        window_start = min(start for _, start in pending.values())
        for symbol in symbols:
            self.gate.mark_attempt(ResourceType.PRICE_HISTORY, symbol, ttl)
        for symbol in backfilling:
            self.gate.mark_attempt(
                ResourceType.PRICE_HISTORY_BACKFILL, sello.format(symbol), backfill_ttl
            )

        try:
            history = self.provider.fetch_history(symbols, window_start, today)
        except ProviderError as exc:
            report.note(
                self._blame_failure(
                    exc, ResourceType.PRICE_HISTORY, symbols, ttl,
                    "No se pudo actualizar el histórico",
                )
            )
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
            if symbol in backfilling:
                # Se pidió el pasado y el proveedor contestó: lo que haya
                # devuelto es todo lo que tiene. Sellarlo impide redescargar
                # cinco años en cada sincronización para los símbolos jóvenes,
                # cuya serie NUNCA va a alcanzar la profundidad pedida.
                self.gate.mark_success(
                    ResourceType.PRICE_HISTORY_BACKFILL,
                    sello.format(symbol),
                    backfill_ttl,
                )

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

        if self._cooling_down(report):
            return report

        symbols = sorted(pending)
        for symbol in symbols:
            self.gate.mark_attempt(ResourceType.FUNDAMENTALS, symbol, ttl)

        try:
            data = self.provider.fetch_fundamentals(symbols)
        except ProviderError as exc:
            report.note(
                self._blame_failure(
                    exc, ResourceType.FUNDAMENTALS, symbols, ttl,
                    "No se pudieron actualizar fundamentales",
                )
            )
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
            # Tres redes, en la frontera, para que aguas abajo nadie tenga que
            # preguntarse si el número es real:
            #
            #   1. Esto no es una empresa (un fondo de bonos, una cesta de oro,
            #      una cripto), o creemos que sí pero no nos consta: ningún
            #      múltiplo significa nada.
            #   2. Es un fondo de acciones: el P/E sí, el valor en libros no.
            #   3. Es una empresa: divisa y magnitud, como siempre.
            #
            # Ver `services/data_quality.py` para el porqué y las mediciones.
            clasificacion = asset_class.classify(asset)
            checked = data_quality.sanitise_multiples(
                {
                    name: getattr(fundamental, name)
                    for name in (
                        *data_quality.VALUATION_MULTIPLES,
                        *data_quality.EARNINGS_INPUTS,
                    )
                },
                quote_currency=asset.currency,
                financial_currency=fundamental.financial_currency,
                asset_class=clasificacion.asset_class,
                is_assumed=clasificacion.is_assumed,
            )
            if checked.has_drops:
                detail = "; ".join(
                    f"{ratio} ({motivo})" for ratio, motivo in checked.dropped.items()
                )
                # Al CONTADOR, no a los avisos: es rutina y enterraba los
                # fallos reales. Ver `RefreshReport.drop`.
                report.drop(symbol, checked.dropped)
                logger.debug("%s: ratios descartados -> %s", symbol, detail)

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

        if self._cooling_down(report):
            return report

        symbols = sorted(pending)
        for symbol in symbols:
            self.gate.mark_attempt(ResourceType.METADATA, symbol, ttl)

        try:
            metadata = self.provider.fetch_metadata(symbols)
        except ProviderError as exc:
            report.note(
                self._blame_failure(
                    exc, ResourceType.METADATA, symbols, ttl,
                    "No se pudieron actualizar metadatos",
                )
            )
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
            asset.fund_category = info.fund_category or asset.fund_category
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

    def refresh_fund_profiles(
        self, assets: list[Asset], *, force: bool = False
    ) -> RefreshReport:
        """Composición, coste y calidad crediticia de los fondos.

        SOLO SE PIDE A LO QUE PUEDE SERLO. Preguntar por el perfil de una
        acción es una llamada garantizada a fallar, y con 500 acciones en el
        universo eso son 500 viajes para recibir el mismo error.

        Un símbolo que no devuelve perfil NO se marca como fallo: no serlo es
        el caso normal -Yahoo tampoco cubre los fondos de la BVC- y apuntarlo
        contra el símbolo lo metería en un backoff exponencial por no ser algo
        que nadie afirmó que fuera. Se sella igual que un acierto para no
        volver a preguntar en treinta días.
        """
        report = RefreshReport()
        ttl = settings.fund_profile_ttl_seconds

        candidatos = [
            a for a in assets if a.asset_type in (AssetType.ETF, AssetType.FUND)
        ]
        pending = {
            a.symbol: a
            for a in candidatos
            if self.gate.should_fetch(ResourceType.FUND_PROFILE, a.symbol, ttl, force=force)
        }
        if not pending:
            return report

        if self._cooling_down(report):
            return report

        symbols = sorted(pending)
        for symbol in symbols:
            self.gate.mark_attempt(ResourceType.FUND_PROFILE, symbol, ttl)

        try:
            profiles = self.provider.fetch_fund_profiles(symbols)
        except ProviderError as exc:
            report.note(
                self._blame_failure(
                    exc, ResourceType.FUND_PROFILE, symbols, ttl,
                    "No se pudieron actualizar los perfiles de fondo",
                )
            )
            self.db.commit()
            return report

        ahora = dt.datetime.now(dt.UTC)
        for symbol, asset in pending.items():
            datos = profiles.get(symbol)
            # Sellar TAMBIÉN el silencio: ver el docstring.
            self.gate.mark_success(ResourceType.FUND_PROFILE, symbol, ttl)
            if datos is None:
                continue

            perfil = self.db.scalar(
                select(FundProfile).where(FundProfile.asset_id == asset.id)
            )
            if perfil is None:
                perfil = FundProfile(asset_id=asset.id)
                self.db.add(perfil)
            perfil.category = datos.category
            perfil.legal_type = datos.legal_type
            perfil.stock_position = datos.stock_position
            perfil.bond_position = datos.bond_position
            perfil.cash_position = datos.cash_position
            perfil.other_position = datos.other_position
            perfil.expense_ratio = datos.expense_ratio
            perfil.credit_ratings = datos.credit_ratings
            perfil.fetched_at = ahora

            # La categoría del perfil es la MISMA que trae `info`, y tenerla
            # aquí permite rellenar el campo del activo aunque el refresco de
            # metadatos no haya pasado todavía.
            if datos.category and not asset.fund_category:
                asset.fund_category = datos.category
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

        if self._cooling_down(report):
            return report

        for base, quote in pending:
            self.gate.mark_attempt(ResourceType.FX, f"{base}{quote}", ttl)

        try:
            rates = self.provider.fetch_fx_rates(pending)
        except ProviderError as exc:
            report.note(
                self._blame_failure(
                    exc, ResourceType.FX,
                    [f"{base}{quote}" for base, quote in pending], ttl,
                    "No se pudieron actualizar tipos de cambio",
                )
            )
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

    def refresh_fx_history(
        self, pairs: list[tuple[str, str]], *, days: int = 0, force: bool = False
    ) -> RefreshReport:
        """Rellena los CIERRES diarios ausentes de cada par de divisas.

        POR QUÉ HACE FALTA. `fx_rates` acumulaba un tipo por día, pero solo
        desde que la aplicación empezó a correr: al escribir esto eran tres
        semanas de USD/COP. Tres semanas no sirven para valorar un histórico, y
        sin eso una cartera con posiciones en COP no puede dibujar su curva de
        valor ni compararse con un índice más allá de ese mes escaso.

        Se pide solo desde el día siguiente al último almacenado, igual que las
        barras de precio, porque un cierre pasado es inmutable. `days` fuerza
        una ventana concreta para la carga inicial.

        NO PISA lo ya guardado (`ON CONFLICT DO NOTHING`). Es deliberado: el
        tipo del día en curso lo escribe `refresh_fx` con la cotización VIVA, y
        el cierre de esa misma fecha llegaría después y lo sustituiría por un
        valor más viejo. Rellenar huecos y corregir el presente son dos cosas
        distintas.
        """
        report = RefreshReport()
        pairs = [(b.upper(), q.upper()) for b, q in pairs if b.upper() != q.upper()]
        if not pairs:
            return report

        ttl = settings.fx_history_ttl_seconds
        today = dt.date.today()
        pending = [
            pair
            for pair in pairs
            if self.gate.should_fetch(
                ResourceType.FX_HISTORY, f"{pair[0]}{pair[1]}", ttl, force=force
            )
        ]
        if not pending:
            return report
        if self._cooling_down(report):
            return report

        stored_range = market_repo.get_fx_date_range(self.db, pending)
        floor = today - dt.timedelta(days=days or settings.fx_history_days)

        rows: list[dict] = []
        for pair in pending:
            key = f"{pair[0]}{pair[1]}"
            self.gate.mark_attempt(ResourceType.FX_HISTORY, key, ttl)

            # De dónde arrancar. No basta con "el día siguiente al último":
            # eso solo sabe avanzar, y en una base que ya tenía unas semanas
            # dejaba intacto todo el hueco anterior. Si la primera fecha
            # almacenada es posterior al suelo de la ventana, falta lo de
            # antes y se pide entero; el ON CONFLICT DO NOTHING hace que el
            # solape no cueste ninguna escritura.
            stored = stored_range.get(pair)
            if stored is None or stored[0] > floor:
                start = floor
            else:
                start = stored[1] + dt.timedelta(days=1)

            if start > today:
                self.gate.mark_success(ResourceType.FX_HISTORY, key, ttl)
                continue

            try:
                fetched = self.provider.fetch_fx_history([pair], start, today)
            except ProviderError as exc:
                report.note(
                    self._blame_failure(
                        exc, ResourceType.FX_HISTORY, [key], ttl,
                        "No se pudo traer el histórico de tipos de cambio",
                    )
                )
                continue

            serie = fetched.get(pair, [])
            for when, rate in serie:
                # El día EN CURSO no se toca: su tipo lo mantiene `refresh_fx`
                # con la cotización viva, y el "cierre" que Yahoo devuelve para
                # hoy es sencillamente el último precio, que quedaría congelado
                # como si la jornada hubiera terminado.
                if when >= today:
                    continue
                rows.append({
                    "base_currency": pair[0],
                    "quote_currency": pair[1],
                    "date": when,
                    "rate": Decimal(str(rate)),
                    "source": "yfinance:close",
                    "fetched_at": dt.datetime.now(dt.UTC),
                })
            self.gate.mark_success(ResourceType.FX_HISTORY, key, ttl)
            report.fx_updated += sum(1 for when, _ in serie if when < today)

        if rows:
            # PISA lo que hubiera, y es deliberado. Una fila de un día pasado
            # escrita por el refresco de jornada guarda la cotización viva del
            # momento en que se pidió, no el cierre: medido sobre USD/COP, esas
            # filas se desviaban del cierre entre 0,1% y 0,9%. Para valorar un
            # día ya cerrado el canónico es el cierre.
            upsert(
                self.db,
                FxRateDaily.__table__,
                rows,
                index_elements=["base_currency", "quote_currency", "date"],
                update_columns=["rate", "source", "fetched_at"],
            )
        self.db.commit()
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
        # El perfil va DESPUÉS de los metadatos: hace falta saber que algo es
        # un fondo para pedírselo, y eso lo fija `refresh_metadata`.
        report.merge(self.refresh_fund_profiles(assets, force=force))
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
        # El histórico va en el job completo y no en el de jornada: son cierres
        # inmutables, igual que las barras de precio.
        report.merge(self.refresh_fx_history(pairs, force=force))
        return report
