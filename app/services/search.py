"""Búsqueda de símbolos para el autocompletado.

Estrategia LOCAL PRIMERO:

1. Se consulta el catálogo (`assets`), que es instantáneo, funciona sin red y
   contiene justo lo que al usuario le interesa: lo que ya sigue o posee.
2. Solo si hacen falta más resultados se va a Yahoo.

El orden importa tanto como el contenido: buscar "AA" cuando ya tienes AAPL
debe poner AAPL arriba, no un ADR brasileño con mejor puntuación en Yahoo.

Sobre la caché: un autocompletado dispara una consulta por pulsación
("n", "nv", "nvi", "nvid"...). Sin caché eso son cuatro llamadas a un proveedor
con rate limit para escribir una palabra. Se usa una caché EN MEMORIA con TTL
en lugar de `data_sync_state` porque son miles de claves efímeras y de bajo
valor: llenar una tabla persistente con ellas la convertiría en basura.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.exceptions import ProviderError
from app.models import Asset, AssetQuote, AssetType
from app.providers.base import MarketProvider, SearchHit
from app.repositories import market as market_repo
from app.schemas.search import SymbolSuggestion
from app.services import universe as universe_service

logger = logging.getLogger(__name__)

MIN_QUERY_LENGTH = 2
SEARCH_CACHE_TTL_SECONDS = 300
SEARCH_CACHE_MAX_ENTRIES = 512

# Una cotización más vieja que esto se marca obsoleta. No se oculta -sigue
# siendo la mejor referencia disponible- pero la interfaz debe decir de cuándo
# es, en vez de presentarla como el precio de ahora.
PRICE_STALE_AFTER = dt.timedelta(hours=24)

_QUOTE_TYPE_MAP = {
    "EQUITY": AssetType.STOCK,
    "ETF": AssetType.ETF,
    "MUTUALFUND": AssetType.FUND,
    "CRYPTOCURRENCY": AssetType.CRYPTO,
    "INDEX": AssetType.OTHER,
    "FUTURE": AssetType.OTHER,
    "CURRENCY": AssetType.OTHER,
}


@dataclass
class _Entry:
    hits: list[SearchHit]
    expires_at: float


class _TtlCache:
    """LRU acotada con expiración. Suficiente para un autocompletado.

    Se descarta al reiniciar el proceso, y está bien: una sugerencia perdida
    cuesta una llamada, no un dato.
    """

    def __init__(self, ttl: int, max_entries: int) -> None:
        self._ttl = ttl
        self._max = max_entries
        self._data: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> list[SearchHit] | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                del self._data[key]
                return None
            # Reinserta para que el más reciente quede al final: así el
            # descarte por tamaño elimina el menos usado.
            del self._data[key]
            self._data[key] = entry
            return entry.hits

    def put(self, key: str, hits: list[SearchHit]) -> None:
        with self._lock:
            if len(self._data) >= self._max:
                oldest = next(iter(self._data))
                del self._data[oldest]
            self._data[key] = _Entry(hits, time.monotonic() + self._ttl)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_cache = _TtlCache(SEARCH_CACHE_TTL_SECONDS, SEARCH_CACHE_MAX_ENTRIES)


def clear_cache() -> None:
    """Vacía la caché de búsqueda. Usado por los tests."""
    _cache.clear()


def _search_local(db: Session, query: str, limit: int) -> list[Asset]:
    """Coincidencias en el catálogo, por símbolo o por nombre.

    Los que empiezan por la consulta van antes que los que solo la contienen:
    escribir "AA" debe sugerir AAPL antes que BRK-AA.
    """
    pattern = f"%{query}%"
    prefix = f"{query}%"

    rows = db.scalars(
        select(Asset)
        .where(
            Asset.is_active.is_(True),
            or_(Asset.symbol.ilike(pattern), Asset.name.ilike(pattern)),
        )
        .order_by(
            Asset.symbol.ilike(prefix).desc(),
            Asset.is_universe.desc(),
            Asset.symbol.asc(),
        )
        .limit(limit)
    ).all()
    return list(rows)


def _quote_fields(quote: AssetQuote | None) -> dict:
    """Extrae precio y su procedencia de una cotización almacenada."""
    if quote is None:
        return {"price": None, "price_as_of": None, "price_is_stale": False}

    reference = quote.quote_time or quote.fetched_at
    aged = reference is not None and (dt.datetime.now(dt.UTC) - reference) > PRICE_STALE_AFTER
    return {
        "price": quote.price,
        "price_as_of": reference,
        # `is_stale` lo marca el pipeline cuando un refresco falla; la edad lo
        # complementa para el caso en que simplemente no se ha sincronizado.
        "price_is_stale": bool(quote.is_stale) or aged,
    }


def _to_suggestion_from_asset(
    asset: Asset, *, held: bool, quote: AssetQuote | None = None
) -> SymbolSuggestion:
    return SymbolSuggestion(
        symbol=asset.symbol,
        name=asset.name,
        exchange=asset.exchange,
        asset_type=asset.asset_type,
        currency=asset.currency,
        sector=asset.sector,
        source="catalog",
        in_catalog=True,
        in_universe=asset.is_universe,
        is_held=held,
        **_quote_fields(quote),
    )


def _to_suggestion_from_hit(hit: SearchHit) -> SymbolSuggestion:
    return SymbolSuggestion(
        symbol=hit.symbol,
        name=hit.name,
        exchange=hit.exchange,
        asset_type=_QUOTE_TYPE_MAP.get(hit.quote_type or "", AssetType.OTHER),
        # Yahoo no devuelve divisa ni sector en la búsqueda. Se dejan en None en
        # lugar de deducirlos de la bolsa: una heurística "NASDAQ -> USD" acierta
        # a menudo y falla justo en los casos que importan.
        currency=None,
        sector=None,
        source="provider",
        in_catalog=False,
        in_universe=False,
        is_held=False,
    )


def search_symbols(
    db: Session,
    provider: MarketProvider,
    query: str,
    *,
    limit: int = 8,
    include_remote: bool = True,
) -> tuple[list[SymbolSuggestion], list[str]]:
    """Devuelve (sugerencias, avisos).

    Nunca lanza por un fallo del proveedor: si Yahoo no responde se sirven las
    coincidencias locales con un aviso. Un autocompletado que se rompe al
    perder la red es peor que uno que sugiere menos.
    """
    query = query.strip()
    warnings: list[str] = []

    if len(query) < MIN_QUERY_LENGTH:
        return [], []

    held_ids = universe_service.held_asset_ids(db)
    local = _search_local(db, query, limit)
    # Los precios ya cacheados salen en UNA consulta y sin tocar la red, así que
    # el desplegable puede mostrarlos. Los resultados de Yahoo no los llevan:
    # ausencia no es engaño, un número inventado sí.
    quotes = market_repo.get_quotes(db, [asset.id for asset in local])
    suggestions = [
        _to_suggestion_from_asset(
            asset, held=asset.id in held_ids, quote=quotes.get(asset.id)
        )
        for asset in local
    ]
    seen = {s.symbol for s in suggestions}

    if not include_remote or len(suggestions) >= limit:
        return suggestions[:limit], warnings

    cache_key = f"{query.casefold()}::{limit}"
    hits = _cache.get(cache_key)

    if hits is None:
        try:
            hits = provider.search_symbols(query, limit)
            _cache.put(cache_key, hits)
        except ProviderError as exc:
            logger.warning("Búsqueda remota fallida para %r: %s", query, exc)
            warnings.append(
                "No se pudo consultar el buscador de Yahoo; solo se muestran "
                "activos ya conocidos."
            )
            hits = []

    for hit in hits:
        if hit.symbol in seen:
            continue
        suggestions.append(_to_suggestion_from_hit(hit))
        seen.add(hit.symbol)
        if len(suggestions) >= limit:
            break

    return suggestions[:limit], warnings


def resolve_symbol(
    db: Session, provider: MarketProvider, symbol: str
) -> SymbolSuggestion | None:
    """Completa divisa, sector y PRECIO de un símbolo elegido. No persiste nada.

    Es una llamada aparte de la búsqueda a propósito: ocurre una sola vez, al
    seleccionar, en lugar de una por pulsación.

    Orden de resolución del precio, de más barato a más caro:

    1. Cotización ya cacheada en `asset_quotes` -sin red-.
    2. Si no hay, o es obsoleta, se pide al proveedor con `fetch_quotes`, que
       usa `fast_info` y es bastante más ligero que el `info` completo.

    No escribe en el catálogo porque también la usa el simulador, que no puede
    tocar la base de datos. El activo se crea al registrar la transacción real.
    """
    symbol = symbol.strip().upper()
    if not symbol:
        return None

    asset = db.scalar(select(Asset).where(Asset.symbol == symbol))
    cached_quote = (
        db.get(AssetQuote, asset.id) if asset is not None else None
    )
    price_fields = _quote_fields(cached_quote)

    # Solo se sale a la red por el precio si no hay uno utilizable. Un precio
    # obsoleto SÍ se intenta refrescar: es justo el caso en que prellenar el
    # formulario con él induciría a error.
    if price_fields["price"] is None or price_fields["price_is_stale"]:
        fresh = _fetch_live_price(provider, symbol)
        if fresh is not None:
            price_fields = fresh

    if asset is not None and asset.sector and asset.currency:
        held = asset.id in universe_service.held_asset_ids(db)
        suggestion = _to_suggestion_from_asset(asset, held=held)
        return suggestion.model_copy(update=price_fields)

    try:
        metadata = provider.fetch_metadata([symbol]).get(symbol)
    except ProviderError as exc:
        logger.warning("No se pudo resolver %s: %s", symbol, exc)
        metadata = None

    if metadata is None:
        # Se devuelve lo que haya en el catálogo antes que nada: un activo sin
        # sector sigue siendo mejor que ninguna respuesta.
        if asset is not None:
            held = asset.id in universe_service.held_asset_ids(db)
            suggestion = _to_suggestion_from_asset(asset, held=held)
            return suggestion.model_copy(update=price_fields)
        return None

    return SymbolSuggestion(
        symbol=symbol,
        name=metadata.name,
        exchange=metadata.exchange,
        asset_type=_QUOTE_TYPE_MAP.get(metadata.asset_type or "", AssetType.STOCK),
        currency=metadata.currency,
        sector=metadata.sector,
        source="provider",
        in_catalog=asset is not None,
        in_universe=bool(asset and asset.is_universe),
        is_held=bool(asset and asset.id in universe_service.held_asset_ids(db)),
        **price_fields,
    )


def _fetch_live_price(provider: MarketProvider, symbol: str) -> dict | None:
    """Cotización en vivo de UN símbolo. None si no se pudo obtener.

    Nunca propaga el fallo: quedarse sin precio de referencia es una molestia,
    pero impedir seleccionar el símbolo por ello sería peor.
    """
    try:
        quote = provider.fetch_quotes([symbol]).get(symbol)
    except ProviderError as exc:
        logger.warning("Sin precio en vivo para %s: %s", symbol, exc)
        return None

    if quote is None:
        return None
    return {
        "price": quote.price,
        "price_as_of": quote.quote_time or dt.datetime.now(dt.UTC),
        "price_is_stale": False,
    }
