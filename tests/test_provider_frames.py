"""Lectura del frame que devuelve `yf.download`.

Un solo símbolo devolvía CERO barras y cero cotizaciones, sin lanzar nada.
Estos tests no tocan la red: construyen los dos formatos de frame a mano,
porque el fallo no estaba en la descarga sino en cómo se leía lo descargado.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.providers.yfinance_client import _sub_frame

COLUMNS = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
INDEX = pd.to_datetime(["2026-09-17", "2026-09-18"])


def multiindex_frame(*symbols: str) -> pd.DataFrame:
    """El formato de `group_by="ticker"`: columnas ('AAPL', 'Close')."""
    columns = pd.MultiIndex.from_product([symbols, COLUMNS], names=["Ticker", "Price"])
    data = [[1.0] * (len(symbols) * len(COLUMNS)) for _ in INDEX]
    return pd.DataFrame(data, index=INDEX, columns=columns)


def flat_frame() -> pd.DataFrame:
    """El formato aplanado que devolvían versiones antiguas con un solo ticker."""
    return pd.DataFrame([[1.0] * len(COLUMNS) for _ in INDEX], index=INDEX, columns=COLUMNS)


def test_a_single_symbol_still_arrives_inside_a_multiindex():
    """EL fallo. Era la única forma en que un símbolo suelto podía leerse.

    El código asumía que con un solo símbolo yfinance aplana el MultiIndex y
    usaba el frame entero. La versión instalada no lo aplana, así que
    `row.get("Close")` daba None en todas las filas y la función devolvía cero
    barras en silencio. Medido antes del arreglo: `fetch_history(["AAPL"])`
    daba 0 barras y `fetch_history(["AAPL", "MSFT"])` daba 9 y 9.
    """
    frame = multiindex_frame("AAPL")
    sub = _sub_frame(frame, "AAPL")

    assert sub is not None
    assert list(sub.columns) == COLUMNS
    assert sub.iloc[0].get("Close") == 1.0


def test_several_symbols_keep_working():
    frame = multiindex_frame("AAPL", "MSFT")
    for symbol in ("AAPL", "MSFT"):
        sub = _sub_frame(frame, symbol)
        assert sub is not None and list(sub.columns) == COLUMNS


def test_a_flat_frame_is_still_understood():
    """Se aceptan las DOS formas a propósito.

    yfinance ha cambiado este comportamiento entre versiones; atarse de nuevo
    a una sola es repetir el fallo en la próxima actualización.
    """
    sub = _sub_frame(flat_frame(), "AAPL")
    assert sub is not None
    assert sub.iloc[0].get("Close") == 1.0


def test_a_symbol_the_download_dropped_is_skipped_not_guessed():
    """Si Yahoo no devolvió ese ticker, no se puede inventar otro."""
    assert _sub_frame(multiindex_frame("AAPL", "MSFT"), "NVDA") is None


@pytest.mark.parametrize("ticker,expected", [
    (("USD", "COP"), "USDCOP=X"),
    (("COP", "USD"), "COPUSD=X"),
])
def test_the_fx_ticker_is_explicit_about_direction(ticker, expected):
    """`COP=X` asume USD como base de forma implícita y es ilegible.

    Equivocar el sentido en una cartera COP/USD produce un error de ~16
    millones de veces.
    """
    from app.providers.yfinance_client import YFinanceClient

    assert YFinanceClient.fx_ticker(*ticker) == expected
