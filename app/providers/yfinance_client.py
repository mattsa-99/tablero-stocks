"""Cliente de yfinance.

yfinance es un scraper no oficial: sin SLA, con rate limits agresivos y no
documentados, y con un esquema de respuesta que cambia sin aviso. Este módulo
asume que FALLARÁ y traduce todos sus modos de fallo a excepciones de dominio.

Reglas que se aplican aquí:
- Import perezoso: el paquete se importa dentro de los métodos, no arriba, para
  que el resto del sistema (y los tests) funcione sin la dependencia instalada.
- Batch obligatorio: una llamada por lote de símbolos, nunca un bucle.
- Ningún campo se da por presente: yfinance devuelve None con frecuencia.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Any

from app.core.exceptions import (
    ProviderRateLimited,
    ProviderUnavailable,
    ProviderUnreachable,
    SymbolNotFound,
)
from app.providers.base import (
    AssetMetadata,
    BarData,
    FundamentalData,
    QuoteData,
    SearchHit,
)
from app.providers.currencies import (
    may_quote_in_minor_units,
    normalize_currency,
    scale,
)

logger = logging.getLogger(__name__)

# Yahoo señaliza el rate limit de varias formas según el endpoint y la versión.
_RATE_LIMIT_MARKERS = ("rate limit", "too many requests", "429")

# Fallos en los que NO se llegó a hablar con Yahoo. Salen textuales del log
# del agente de launchd, donde son el 80% de los errores:
#
#   Failed to perform, curl: (6) Could not resolve host: query2.finance...
#   Failed to perform, curl: (28) Operation timed out after 946059 millise...
#
# "failed to perform" es el envoltorio de curl_cffi para CUALQUIER fallo de
# transporte, así que por sí solo ya identifica la clase entera; los demás
# marcadores cubren los caminos que no pasan por curl.
_CONNECTION_MARKERS = (
    "failed to perform",
    "could not resolve host",
    "temporary failure in name resolution",
    "operation timed out",
    "timed out",
    "timeout",
    "failed to connect",
    "connection refused",
    "connection reset",
    "connection aborted",
    "network is unreachable",
    "max retries exceeded",
    "remote disconnected",
    "empty reply from server",
    "ssl connect error",
)


def _sub_frame(frame, symbol: str):
    """Las columnas de UN símbolo dentro del frame de `yf.download`.

    Aquí vivía un fallo silencioso y caro. El código asumía que con un solo
    símbolo yfinance aplana el MultiIndex y hacía `frame[symbol] if
    len(symbols) > 1 else frame`. La versión instalada NO lo aplana con
    `group_by="ticker"`: las columnas siguen siendo `('AAPL', 'Close')`, así
    que `row.get("Close")` devolvía None en todas las filas y la función
    entregaba CERO barras sin lanzar nada.

    Medido antes del arreglo:

        fetch_history(["AAPL"])          -> 0 barras
        fetch_history(["AAPL", "MSFT"])  -> 9 y 9 barras

    Lo que rompía es justo el camino de un símbolo suelto: la carga perezosa
    de `ensure_data_for` y los que el usuario escribe en `?symbols=`, que se
    traen EN LÍNEA precisamente para poder puntuarlos. Sin histórico no se
    puntúan y desaparecían del ranking que se había pedido a propósito.

    Se prueban las dos formas en vez de fijar una: yfinance ha cambiado este
    comportamiento entre versiones y volver a atarse a una sola es repetir el
    fallo en la siguiente actualización.
    """
    columns = getattr(frame, "columns", None)
    if columns is not None and getattr(columns, "nlevels", 1) > 1:
        try:
            return frame[symbol]
        except KeyError:
            return None
    # Frame plano: solo puede ser de este símbolo si se pidió uno.
    return frame


def _clean(value: Any) -> float | None:
    """Normaliza un campo numérico de yfinance.

    Devuelve None ante ausencia, NaN o infinito. Los NaN son especialmente
    peligrosos: se propagan silenciosamente por toda la aritmética y acaban
    produciendo scores NaN que ordenan de forma impredecible.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _clean_int(value: Any) -> int | None:
    number = _clean(value)
    return None if number is None else int(number)


def _translate_error(exc: Exception, context: str) -> Exception:
    """Clasifica un fallo del proveedor en las tres cosas distintas que puede ser.

    El orden importa: el rate limit se comprueba PRIMERO porque un 429 llega
    por una conexión que funcionó, y confundirlo con una caída de red haría
    que reintentáramos pronto contra un proveedor que pidió justo lo
    contrario.
    """
    text = str(exc).lower()
    if any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return ProviderRateLimited(f"Rate limit de Yahoo Finance en {context}: {exc}")
    if any(marker in text for marker in _CONNECTION_MARKERS):
        return ProviderUnreachable(f"Sin conexión con el proveedor en {context}: {exc}")
    return ProviderUnavailable(f"Fallo del proveedor en {context}: {exc}")


_yf_configured = False


def _configure_yfinance(yfinance: Any) -> None:
    """Ajustes globales de yfinance, una sola vez por proceso.

    **Silenciar su logger no es esconder errores.** yfinance escribe una línea
    por símbolo sin datos MÁS un resumen de "N Failed downloads", y en un
    refresco del universo eso son decenas de líneas por las que no se puede
    hacer nada: el mismo hecho ya se registra aquí como "Sin precio para X" y
    llega a la interfaz como aviso. Mantener las dos copias solo consigue que
    la terminal sea ilegible justo cuando hay algo que leer.

    **`retries` viene de fábrica en 0.** Se sube a 2 porque el reintento de
    yfinance solo cubre errores de RED -timeouts y conexiones cortadas, no el
    429, que tiene su propio tipo y no cuenta como transitorio-, y espera
    2^intento segundos entre uno y otro. Es decir: ayuda con el wifi y no
    puede convertirse en un martilleo contra un proveedor que ya está
    rechazando peticiones.
    """
    global _yf_configured
    if _yf_configured:
        return
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    try:
        yfinance.config.network.retries = 2
    except Exception:  # pragma: no cover - versión sin config de red
        logger.debug("Esta versión de yfinance no expone config.network.retries")
    _yf_configured = True


class YFinanceClient:
    """Implementación de MarketProvider sobre yfinance."""

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout
        # `.info` alimenta DOS refrescos distintos -metadatos y fundamentales-
        # que en una primera ingesta se ejecutan seguidos sobre los mismos
        # símbolos, duplicando la llamada más cara del pipeline (~0,76 s).
        # Con ~600 símbolos nuevos eso son 15 minutos en vez de 8.
        #
        # El memo es POR INSTANCIA, y `get_provider()` crea una por petición:
        # su vida es la de un refresco, no la del proceso. Así no puede servir
        # datos rancios en la siguiente sincronización, que es justo el riesgo
        # que tendría una caché de módulo.
        self._info_cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _yf():
        try:
            import yfinance
        except ImportError as exc:  # pragma: no cover - entorno mal instalado
            raise ProviderUnavailable(
                "yfinance no está instalado: pip install yfinance"
            ) from exc
        _configure_yfinance(yfinance)
        return yfinance

    # ------------------------------------------------------------------
    # Cotizaciones
    # ------------------------------------------------------------------

    def _download_quotes(
        self, symbols: list[str], divisors: dict[str, float]
    ) -> dict[str, QuoteData]:
        """Una pasada de descarga agrupada. No avisa de lo que falte."""
        yf = self._yf()
        try:
            frame = yf.download(
                tickers=symbols,
                period="5d",
                interval="1d",
                auto_adjust=False,
                actions=False,
                group_by="ticker",
                progress=False,
                threads=True,
                timeout=self.timeout,
            )
        except Exception as exc:
            raise _translate_error(exc, "fetch_quotes") from exc

        if frame is None or frame.empty:
            return {}

        now = dt.datetime.now(dt.UTC)
        quotes: dict[str, QuoteData] = {}
        for symbol in symbols:
            sub = _sub_frame(frame, symbol)
            if sub is None:
                continue

            closes = [v for v in (_clean(x) for x in sub["Close"]) if v is not None]
            if not closes:
                continue

            divisor = divisors.get(symbol, 1.0)
            quotes[symbol] = QuoteData(
                symbol=symbol,
                price=closes[-1] / divisor,
                previous_close=scale(closes[-2] if len(closes) > 1 else None, divisor),
                currency=None,
                quote_time=now,
            )
        return quotes

    def fetch_quotes(self, symbols: list[str]) -> dict[str, QuoteData]:
        """Cotizaciones del lote en descargas agrupadas, no una por símbolo.

        `yf.Tickers(...)` es PEREZOSO: construirlo no cuesta nada y cada
        `fast_info` que se le pide después es un viaje de ida y vuelta propio.
        Medido: 3 ms de construcción y 0,42 s por símbolo, o sea 208 s para el
        universo de 494 -y una ráfaga de 494 peticiones que Yahoo corta a
        mitad con un 429-. Con `yf.download` el mismo universo tarda 81 s y
        no dispara el límite.

        Se piden 5 días y se usan las dos últimas barras CON precio: la última
        es la cotización -durante la sesión Yahoo va actualizando la barra del
        día en curso- y la anterior el cierre previo. Buscar la última con
        precio, y no la última fila, es lo que evita quedarse a cero un
        festivo.

        **Cinco días y no un mes, aunque un mes devuelva más.** Con ventana
        larga, un símbolo cuya serie lleva semanas parada devuelve igualmente
        un número y lo presentaría como el precio de hoy: AVB da 68,14 con
        `period="1mo"` -su última barra es del 24 de agosto- cuando cotiza a
        184. Con cinco días no devuelve nada, que es el fallo correcto: sin
        cotización se conserva el último valor y se marca `is_stale`, en vez
        de inventar una caída del 63%.

        **Reintento acotado de los rezagados.** Una descarga grande deja caer
        símbolos bajo carga: en el universo completo faltaron 18, y al volver
        a pedir solo esos 12 aparecieron en 3,5 s. Se reintenta UNA vez y solo
        si la primera pasada trajo algo: que no venga nada no son rezagados,
        es una caída o un rate limit, y repetir entonces solo dobla la carga
        justo cuando el proveedor pide que pares.

        NO devuelve divisa, y es deliberado: `yf.download` no la da. Dejarla
        en None hace que el consumidor use la del activo, fijada por los
        metadatos, que es donde `normalize_currency` ya corrió. Poner un "USD"
        por defecto marcaría como dólares los 22 tickers de la BVC. Lo que sí
        se aplica en esta frontera es el divisor de subunidad: `GBp` llega en
        peniques sin ninguna marca, con el mismo sondeo acotado que ya usa el
        histórico.
        """
        if not symbols:
            return {}

        divisors = self._minor_unit_divisors(symbols)
        quotes = self._download_quotes(symbols, divisors)

        missing = [s for s in symbols if s not in quotes]
        if quotes and missing:
            logger.info("Reintentando %d símbolos rezagados del lote", len(missing))
            quotes.update(self._download_quotes(missing, divisors))

        for symbol in symbols:
            if symbol not in quotes:
                logger.warning("Sin precio para %s", symbol)

        return quotes

    # ------------------------------------------------------------------
    # Histórico
    # ------------------------------------------------------------------

    def _minor_unit_divisors(self, symbols: list[str]) -> dict[str, float]:
        """Divisor de subunidad por símbolo, sondeando lo mínimo imprescindible.

        `yf.download` NO devuelve la divisa, así que el histórico de la LSE
        llega en peniques sin ninguna marca. Se resuelve preguntando por la
        divisa, pero SOLO en las bolsas donde la subunidad es posible: para el
        resto del universo esto no cuesta ni una llamada.

        Un fallo del sondeo devuelve divisor 1.0, que es el sesgo correcto: en
        el peor caso deja el precio como venía en lugar de dividir por 100 un
        valor que quizá ya estaba bien.
        """
        candidates = [s for s in symbols if may_quote_in_minor_units(s)]
        if not candidates:
            return {}

        yf = self._yf()
        divisors: dict[str, float] = {}
        try:
            tickers = yf.Tickers(" ".join(candidates))
        except Exception as exc:
            logger.warning("No se pudo sondear la divisa de %s: %s", candidates, exc)
            return {}

        for symbol in candidates:
            try:
                raw = getattr(tickers.tickers[symbol].fast_info, "currency", None)
            except Exception as exc:
                logger.warning("Sin divisa para %s al sondear subunidad: %s", symbol, exc)
                continue
            _, divisor = normalize_currency(raw)
            if divisor != 1.0:
                divisors[symbol] = divisor
                logger.info(
                    "%s cotiza en subunidad (%s): el histórico se divide entre %g",
                    symbol, raw, divisor,
                )
        return divisors

    def fetch_history(
        self, symbols: list[str], start: dt.date, end: dt.date
    ) -> dict[str, list[BarData]]:
        """Barras diarias por símbolo. Una sola llamada para todo el lote."""
        if not symbols:
            return {}
        yf = self._yf()
        divisors = self._minor_unit_divisors(symbols)

        try:
            frame = yf.download(
                tickers=symbols,
                start=start.isoformat(),
                end=(end + dt.timedelta(days=1)).isoformat(),
                interval="1d",
                auto_adjust=False,  # necesitamos close Y adj_close por separado
                actions=False,
                group_by="ticker",
                progress=False,
                threads=True,
                timeout=self.timeout,
            )
        except Exception as exc:
            raise _translate_error(exc, "fetch_history") from exc

        if frame is None or frame.empty:
            return {}

        result: dict[str, list[BarData]] = {}
        for symbol in symbols:
            sub = _sub_frame(frame, symbol)
            if sub is None:
                continue

            divisor = divisors.get(symbol, 1.0)
            bars: list[BarData] = []
            for index, row in sub.iterrows():
                close = _clean(row.get("Close"))
                if close is None:
                    continue  # días sin cotización (festivos del mercado)
                bars.append(
                    BarData(
                        date=index.date(),
                        open=scale(_clean(row.get("Open")), divisor),
                        high=scale(_clean(row.get("High")), divisor),
                        low=scale(_clean(row.get("Low")), divisor),
                        close=close / divisor,
                        adj_close=scale(_clean(row.get("Adj Close")), divisor),
                        # El volumen son títulos, no dinero: NO se escala.
                        volume=_clean_int(row.get("Volume")),
                    )
                )
            if bars:
                result[symbol] = bars

        return result

    # ------------------------------------------------------------------
    # Metadatos y fundamentales
    # ------------------------------------------------------------------

    def _info(self, symbol: str) -> dict[str, Any]:
        cached = self._info_cache.get(symbol)
        if cached is not None:
            return cached

        yf = self._yf()
        try:
            info = yf.Ticker(symbol).info
        except Exception as exc:
            raise _translate_error(exc, f"info({symbol})") from exc

        # Yahoo responde 200 con un payload vacío para tickers inexistentes.
        if not info or len(info) <= 1:
            raise SymbolNotFound(f"Símbolo no encontrado en Yahoo Finance: {symbol}")

        # Solo se cachea el acierto. Cachear el fallo convertiría un 429
        # pasajero en un "no existe" para el resto del refresco, y el símbolo
        # se desactivaría (`is_active = False`) por un problema de red.
        self._info_cache[symbol] = info
        return info

    def fetch_metadata(self, symbols: list[str]) -> dict[str, AssetMetadata]:
        result: dict[str, AssetMetadata] = {}
        for symbol in symbols:
            try:
                info = self._info(symbol)
            except SymbolNotFound:
                logger.info("Ticker no verificable: %s", symbol)
                continue
            result[symbol] = AssetMetadata(
                symbol=symbol,
                name=info.get("longName") or info.get("shortName"),
                currency=normalize_currency(info.get("currency"))[0],
                exchange=info.get("exchange"),
                sector=info.get("sector"),
                industry=info.get("industry"),
                country=info.get("country"),
                asset_type=(info.get("quoteType") or "").upper() or None,
            )
        return result

    def fetch_fundamentals(self, symbols: list[str]) -> dict[str, FundamentalData]:
        result: dict[str, FundamentalData] = {}
        for symbol in symbols:
            try:
                info = self._info(symbol)
            except SymbolNotFound:
                continue
            except ProviderRateLimited:
                raise  # el rate limit sí debe cortar el lote entero
            result[symbol] = FundamentalData(
                symbol=symbol,
                market_cap=_clean(info.get("marketCap")),
                trailing_pe=_clean(info.get("trailingPE")),
                forward_pe=_clean(info.get("forwardPE")),
                price_to_book=_clean(info.get("priceToBook")),
                price_to_sales=_clean(info.get("priceToSalesTrailing12Months")),
                ev_to_ebitda=_clean(info.get("enterpriseToEbitda")),
                profit_margin=_clean(info.get("profitMargins")),
                return_on_equity=_clean(info.get("returnOnEquity")),
                debt_to_equity=_clean(info.get("debtToEquity")),
                revenue_growth=_clean(info.get("revenueGrowth")),
                earnings_growth=_clean(info.get("earningsGrowth")),
                eps_trailing=_clean(info.get("trailingEps")),
                dividend_yield=_clean(info.get("dividendYield")),
                payout_ratio=_clean(info.get("payoutRatio")),
                beta=_clean(info.get("beta")),
                fifty_two_week_high=_clean(info.get("fiftyTwoWeekHigh")),
                fifty_two_week_low=_clean(info.get("fiftyTwoWeekLow")),
                financial_currency=normalize_currency(
                    info.get("financialCurrency")
                )[0],
                raw={k: v for k, v in info.items() if isinstance(v, (int, float, str))},
            )
        return result

    # ------------------------------------------------------------------
    # Búsqueda de símbolos
    # ------------------------------------------------------------------

    def search_symbols(self, query: str, limit: int = 8) -> list[SearchHit]:
        """Busca activos por nombre o ticker parcial.

        `enable_fuzzy_query=True` es lo que hace que "nvi" encuentre NVIDIA en
        lugar de exigir el ticker exacto, que es justo el problema que esta
        búsqueda resuelve.

        Se piden 0 noticias y 0 listas: el payload por defecto trae artículos y
        colecciones que aquí no se usan y multiplican el tamaño de la respuesta
        en un endpoint que se llama con cada pulsación de tecla.
        """
        query = query.strip()
        if not query:
            return []

        yf = self._yf()
        try:
            search = yf.Search(
                query,
                max_results=limit,
                news_count=0,
                lists_count=0,
                enable_fuzzy_query=True,
                raise_errors=True,
                timeout=self.timeout,
            )
            quotes = search.quotes or []
        except Exception as exc:
            raise _translate_error(exc, f"search({query!r})") from exc

        hits: list[SearchHit] = []
        for row in quotes:
            symbol = (row.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            hits.append(
                SearchHit(
                    symbol=symbol,
                    # longname es más descriptivo; shortname es el respaldo.
                    name=row.get("longname") or row.get("shortname"),
                    exchange=row.get("exchDisp") or row.get("exchange"),
                    quote_type=(row.get("quoteType") or "").upper() or None,
                )
            )
        return hits

    # ------------------------------------------------------------------
    # Divisas
    # ------------------------------------------------------------------

    @staticmethod
    def fx_ticker(base: str, quote: str) -> str:
        """Ticker de FX en Yahoo: EURUSD=X, USDCOP=X.

        Se usa la forma explícita de 6 letras y no la abreviada (COP=X) porque
        esta última asume USD como base de forma implícita: al leerla es
        imposible saber el sentido de la cotización, y equivocarse produce un
        error de un factor de ~16 millones en una cartera COP/USD.
        """
        return f"{base.upper()}{quote.upper()}=X"

    def fetch_fx_history(
        self, pairs: list[tuple[str, str]], start: dt.date, end: dt.date
    ) -> dict[tuple[str, str], list[tuple[dt.date, float]]]:
        """Cierres diarios de un par de divisas.

        Es la MISMA descarga que la de un activo -un par de divisas en Yahoo es
        un ticker más, `USDCOP=X`- así que reutiliza `fetch_history` en lugar de
        duplicar el manejo de errores, el lote y el timeout.

        Devuelve CIERRES, no cotizaciones vivas. La diferencia importa: el tipo
        del día en curso se sigue tomando de `fetch_fx_rates`, que da el precio
        de ahora, mientras que esto rellena los días ya cerrados. Mezclarlos al
        revés pondría un cierre de ayer como tipo de hoy.

        El sondeo de subunidad no interfiere: solo mira los sufijos `.L`, `.TA`
        y `.JO`, y un par de divisas termina en `=X`.
        """
        if not pairs:
            return {}
        tickers = {self.fx_ticker(base, quote): (base.upper(), quote.upper())
                   for base, quote in pairs}
        bars_by_ticker = self.fetch_history(list(tickers), start, end)

        result: dict[tuple[str, str], list[tuple[dt.date, float]]] = {}
        for ticker, pair in tickers.items():
            bars = bars_by_ticker.get(ticker)
            if not bars:
                continue
            # Un tipo de cambio no negativo ni nulo: un 0 aquí produciría una
            # división por cero al invertir el par.
            serie = [(bar.date, bar.close) for bar in bars if bar.close > 0]
            if serie:
                result[pair] = serie
        return result

    def fetch_fx_rates(self, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], float]:
        if not pairs:
            return {}
        tickers = [self.fx_ticker(base, quote) for base, quote in pairs]
        quotes = self.fetch_quotes(tickers)

        result: dict[tuple[str, str], float] = {}
        for (base, quote), ticker in zip(pairs, tickers, strict=True):
            data = quotes.get(ticker)
            if data is not None and data.price > 0:
                result[(base.upper(), quote.upper())] = data.price
        return result
