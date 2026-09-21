"""Contratos de la capa de proveedores externos.

Los servicios dependen de ESTAS dataclasses, nunca de objetos de yfinance.
Esa frontera es lo que permite testear toda la lógica de caché, P&L y scoring
con fixtures grabados, sin red y sin tener yfinance instalado.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class QuoteData:
    symbol: str
    price: float
    previous_close: float | None
    # None cuando el proveedor no la informa: `yf.download` agrupa el lote en
    # una sola llamada pero no devuelve divisa. Quien consuma esto usa la del
    # activo, fijada por los metadatos; poner un "USD" por defecto aquí
    # marcaría como dólares a los tickers de la BVC, que cotizan en COP.
    currency: str | None
    quote_time: dt.datetime | None


@dataclass(frozen=True)
class BarData:
    date: dt.date
    open: float | None
    high: float | None
    low: float | None
    close: float
    adj_close: float | None
    volume: int | None


@dataclass(frozen=True)
class AssetMetadata:
    symbol: str
    name: str | None = None
    currency: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    asset_type: str | None = None
    # Categoría Morningstar, solo para fondos. Ver `Asset.fund_category`.
    fund_category: str | None = None


@dataclass(frozen=True)
class FundProfile:
    """Lo que `funds_data` da de FIABLE sobre un fondo.

    Deliberadamente parcial. Del payload completo se descartan dos cosas que
    se probaron y no sirven:

    - `bond_holdings.Duration`: medido contra lo que esos fondos son por
      mandato, SJNK -de corto plazo- salía con 6,48 y TLT -de 20+ años- con
      3,60. No es un factor de escala ni otra unidad: no hay patrón.
    - `sector_weightings`: para SJNK devuelve `communication_services: 1.0`
      en un fondo que es 98,7% bonos.

    Lo que sí se comprobó estable en los 12 fondos probados: la categoría, el
    tipo legal, el reparto por clase de activo, el ratio de gastos y, en los
    de bonos, el reparto por calificación crediticia.
    """

    symbol: str
    category: str | None = None
    legal_type: str | None = None
    # Reparto del patrimonio. Es el dato DURO que dice qué hay dentro, y el
    # único que no depende de que el proveedor haya categorizado el fondo.
    stock_position: float | None = None
    bond_position: float | None = None
    cash_position: float | None = None
    other_position: float | None = None
    # Fracción anual sobre el patrimonio (0.0003 = 0,03%).
    expense_ratio: float | None = None
    # {"aaa": 0.008, "bb": 0.5483, ...} para los fondos de renta fija.
    credit_ratings: dict[str, float] | None = None


@dataclass(frozen=True)
class SearchHit:
    """Un resultado de búsqueda de símbolos.

    Deliberadamente pobre: symbol, nombre, bolsa y tipo es todo lo que el
    endpoint de búsqueda de Yahoo devuelve de forma fiable. La divisa y el
    sector NO vienen aquí -llegan al resolver el símbolo elegido-, y fingir que
    sí obligaría a inventarlos.
    """

    symbol: str
    name: str | None
    exchange: str | None
    quote_type: str | None


@dataclass(frozen=True)
class FundamentalData:
    symbol: str
    market_cap: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    price_to_book: float | None = None
    price_to_sales: float | None = None
    ev_to_ebitda: float | None = None
    profit_margin: float | None = None
    return_on_equity: float | None = None
    debt_to_equity: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None
    eps_trailing: float | None = None
    dividend_yield: float | None = None
    payout_ratio: float | None = None
    beta: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    # Divisa en que la empresa REPORTA sus cuentas, que no siempre es aquella
    # en que cotiza. Es el dato que delata los ratios precio/contabilidad que
    # el proveedor no convierte (ver `services/data_quality.py`).
    financial_currency: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class MarketProvider(Protocol):
    """Interfaz que implementan YFinanceClient y el doble de test."""

    def fetch_quotes(self, symbols: list[str]) -> dict[str, QuoteData]: ...

    def fetch_history(
        self, symbols: list[str], start: dt.date, end: dt.date
    ) -> dict[str, list[BarData]]: ...

    def fetch_fx_history(
        self, pairs: list[tuple[str, str]], start: dt.date, end: dt.date
    ) -> dict[tuple[str, str], list[tuple[dt.date, float]]]: ...

    def fetch_metadata(self, symbols: list[str]) -> dict[str, AssetMetadata]: ...

    def fetch_fundamentals(self, symbols: list[str]) -> dict[str, FundamentalData]: ...

    def fetch_fund_profiles(self, symbols: list[str]) -> dict[str, FundProfile]: ...

    def search_symbols(self, query: str, limit: int) -> list[SearchHit]: ...
