"""Doble de test del proveedor de mercado.

Implementa el Protocol `MarketProvider` con datos grabados. Los tests no tocan
la red: son deterministas, rápidos y ejecutables sin conexión. La existencia
de esta clase es la justificación de que `providers/base.py` defina dataclasses
propias en lugar de devolver objetos de yfinance.
"""

from __future__ import annotations

import datetime as dt
import math

from app.core.exceptions import (
    ProviderRateLimited,
    ProviderUnavailable,
    ProviderUnreachable,
)
from app.providers.base import (
    AssetMetadata,
    BarData,
    FundamentalData,
    QuoteData,
    SearchHit,
)


def synthetic_series(
    start_price: float,
    days: int,
    *,
    drift: float = 0.0,
    amplitude: float = 0.0,
    end: dt.date | None = None,
) -> list[BarData]:
    """Serie diaria determinista.

    `drift` es el retorno compuesto por día y `amplitude` la magnitud de una
    oscilación sinusoidal que genera volatilidad controlada. Permite construir
    activos con momentum y riesgo conocidos de antemano, y por tanto verificar
    que el ranking los ordena como debe.
    """
    end = end or dt.date.today()
    bars: list[BarData] = []
    for index in range(days):
        day = end - dt.timedelta(days=days - 1 - index)
        trend = start_price * ((1.0 + drift) ** index)
        wave = 1.0 + amplitude * math.sin(index / 7.0)
        close = trend * wave
        bars.append(
            BarData(
                date=day,
                open=close,
                high=close * 1.01,
                low=close * 0.99,
                close=close,
                adj_close=close,
                volume=1_000_000,
            )
        )
    return bars


class FakeProvider:
    """Proveedor controlable. `fail_with` fuerza el camino de error."""

    def __init__(
        self,
        *,
        quotes: dict[str, QuoteData] | None = None,
        history: dict[str, list[BarData]] | None = None,
        metadata: dict[str, AssetMetadata] | None = None,
        fundamentals: dict[str, FundamentalData] | None = None,
        fx: dict[tuple[str, str], float] | None = None,
        fx_history: dict[tuple[str, str], list[tuple[dt.date, float]]] | None = None,
        search: list[SearchHit] | None = None,
        fail_with: Exception | None = None,
    ) -> None:
        self.quotes = quotes or {}
        self.history = history or {}
        self.metadata = metadata or {}
        self.fundamentals = fundamentals or {}
        self.fx = fx or {}
        self.fx_history = fx_history or {}
        self.search = search or []
        self.fail_with = fail_with
        self.calls: list[tuple[str, tuple]] = []

    def _record(self, name: str, *args) -> None:
        self.calls.append((name, args))
        if self.fail_with is not None:
            raise self.fail_with

    def call_count(self, name: str) -> int:
        return sum(1 for call, _ in self.calls if call == name)

    def fetch_quotes(self, symbols: list[str]) -> dict[str, QuoteData]:
        self._record("fetch_quotes", tuple(symbols))
        return {s: self.quotes[s] for s in symbols if s in self.quotes}

    def fetch_history(
        self, symbols: list[str], start: dt.date, end: dt.date
    ) -> dict[str, list[BarData]]:
        self._record("fetch_history", tuple(symbols), start, end)
        return {
            s: [b for b in self.history[s] if start <= b.date <= end]
            for s in symbols
            if s in self.history
        }

    def fetch_metadata(self, symbols: list[str]) -> dict[str, AssetMetadata]:
        self._record("fetch_metadata", tuple(symbols))
        return {s: self.metadata[s] for s in symbols if s in self.metadata}

    def fetch_fundamentals(self, symbols: list[str]) -> dict[str, FundamentalData]:
        self._record("fetch_fundamentals", tuple(symbols))
        return {s: self.fundamentals[s] for s in symbols if s in self.fundamentals}

    def fetch_fx_rates(self, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], float]:
        self._record("fetch_fx_rates", tuple(pairs))
        return {p: self.fx[p] for p in pairs if p in self.fx}

    def fetch_fx_history(
        self, pairs: list[tuple[str, str]], start: dt.date, end: dt.date
    ) -> dict[tuple[str, str], list[tuple[dt.date, float]]]:
        self._record("fetch_fx_history", tuple(pairs), start, end)
        return {
            p: [(d, r) for d, r in self.fx_history[p] if start <= d <= end]
            for p in pairs
            if p in self.fx_history
        }

    def search_symbols(self, query: str, limit: int = 8) -> list[SearchHit]:
        self._record("search_symbols", query, limit)
        needle = query.casefold()
        matches = [
            hit
            for hit in self.search
            if needle in hit.symbol.casefold() or needle in (hit.name or "").casefold()
        ]
        return matches[:limit]


def quote(symbol: str, price: float, previous: float | None = None, currency: str = "USD"):
    return QuoteData(
        symbol=symbol,
        price=price,
        previous_close=previous,
        currency=currency,
        quote_time=dt.datetime.now(dt.UTC),
    )


def metadata(symbol: str, sector: str, currency: str = "USD", name: str | None = None):
    return AssetMetadata(
        symbol=symbol,
        name=name or f"{symbol} Inc.",
        currency=currency,
        exchange="NMS",
        sector=sector,
        industry=f"{sector} sub",
        country="United States",
        asset_type="EQUITY",
    )


def fundamentals(symbol: str, **kwargs) -> FundamentalData:
    return FundamentalData(symbol=symbol, **kwargs)


def hit(symbol: str, name: str, exchange: str = "NASDAQ", quote_type: str = "EQUITY"):
    return SearchHit(symbol=symbol, name=name, exchange=exchange, quote_type=quote_type)


RATE_LIMIT = ProviderRateLimited("429 Too Many Requests")
UNAVAILABLE = ProviderUnavailable("timeout")

# Textuales del log del agente de launchd: son el 80% de los fallos reales.
DNS_DOWN = ProviderUnreachable(
    "Sin conexión con el proveedor en fetch_quotes: Failed to perform, "
    "curl: (6) Could not resolve host: query2.finance.yahoo.com"
)
