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
    text = str(exc).lower()
    if any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return ProviderRateLimited(f"Rate limit de Yahoo Finance en {context}: {exc}")
    return ProviderUnavailable(f"Fallo del proveedor en {context}: {exc}")


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
        return yfinance

    # ------------------------------------------------------------------
    # Cotizaciones
    # ------------------------------------------------------------------

    def fetch_quotes(self, symbols: list[str]) -> dict[str, QuoteData]:
        if not symbols:
            return {}
        yf = self._yf()
        quotes: dict[str, QuoteData] = {}

        try:
            tickers = yf.Tickers(" ".join(symbols))
        except Exception as exc:
            raise _translate_error(exc, "fetch_quotes") from exc

        for symbol in symbols:
            try:
                info = tickers.tickers[symbol].fast_info
                price = _clean(getattr(info, "last_price", None))
                if price is None:
                    logger.warning("Sin precio para %s", symbol)
                    continue
                # La divisa se normaliza ANTES de usar el precio: "GBp" trae
                # peniques, y pasarlos como GBP infla la posición 100 veces.
                currency, divisor = normalize_currency(
                    getattr(info, "currency", None)
                )
                quotes[symbol] = QuoteData(
                    symbol=symbol,
                    price=scale(price, divisor),
                    previous_close=scale(
                        _clean(getattr(info, "previous_close", None)), divisor
                    ),
                    currency=currency or "USD",
                    quote_time=dt.datetime.now(dt.UTC),
                )
            except KeyError:
                logger.warning("Símbolo desconocido en el lote: %s", symbol)
            except Exception as exc:  # un símbolo roto no debe tumbar el lote
                logger.warning("Error obteniendo cotización de %s: %s", symbol, exc)

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
            try:
                # Con un solo símbolo yfinance aplana el MultiIndex.
                sub = frame[symbol] if len(symbols) > 1 else frame
            except KeyError:
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
