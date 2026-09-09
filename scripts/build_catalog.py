"""Genera `app/data/catalog.json` verificando cada candidato contra Yahoo.

    python scripts/build_catalog.py            # verifica y escribe el catálogo
    python scripts/build_catalog.py --dry-run  # solo informa, no escribe

Por qué se verifica en lugar de confiar en la lista: un catálogo escrito a
mano acumula tickers mal escritos, deslistados o renombrados, y cada uno se
convierte en un fallo silencioso el día que alguien lo busca y lo compra. Aquí
un símbolo entra SOLO si Yahoo devuelve precio y divisa para él.

La verificación va en dos pasadas de coste muy distinto:

  1. `fast_info` en lote -barata- resuelve existencia, divisa y precio. Es la
     que descarta.
  2. `.info` -unos 0,76 s por símbolo- solo para los supervivientes, y aporta
     nombre, bolsa, sector y tipo.

El resultado de ambas se cachea en disco para poder reanudar sin repetir la
descarga: Yahoo aplica un rate limit no documentado y perder media hora de
verificación por un 429 al final sería absurdo.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.providers.currencies import normalize_currency  # noqa: E402
from scripts.candidates import all_candidates, in_universe  # noqa: E402

CATALOG_PATH = ROOT / "app" / "data" / "catalog.json"
CACHE_PATH = Path("/private/tmp/claude-501/catalog_build_cache.json")

BATCH = 40
PAUSE = 1.5

# quoteType de Yahoo -> AssetType del dominio.
QUOTE_TYPES = {
    "EQUITY": "STOCK",
    "ETF": "ETF",
    "MUTUALFUND": "FUND",
    "CRYPTOCURRENCY": "CRYPTO",
    "FUTURE": "OTHER",
    "INDEX": "OTHER",
    "CURRENCY": "OTHER",
}


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {"fast": {}, "info": {}}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache))


def verify_existence(
    symbols: list[str], cache: dict, *, retry_failures: bool = False
) -> dict[str, dict]:
    """Pasada 1: ¿existe y en qué divisa cotiza? Barata, en lote.

    `retry_failures` vuelve a preguntar por los que fallaron. Importa: un 429
    o un corte de red se cachearían como "no existe" y expulsarían del catálogo
    a un valor perfectamente real (le pasó a MMC y a ROG.SW en la primera
    pasada). Un descarte solo es de fiar si se ha reintentado.
    """
    import yfinance as yf

    if retry_failures:
        for symbol in [s for s, v in cache["fast"].items() if not v]:
            del cache["fast"][symbol]

    pending = [s for s in symbols if s not in cache["fast"]]
    for start in range(0, len(pending), BATCH):
        chunk = pending[start : start + BATCH]
        print(f"  verificando {start + 1}-{start + len(chunk)} de {len(pending)}...")
        try:
            tickers = yf.Tickers(" ".join(chunk))
        except Exception as exc:
            print(f"  ! lote fallido: {exc}")
            continue
        for symbol in chunk:
            try:
                fi = tickers.tickers[symbol].fast_info
                price = fi.last_price
                raw_currency = fi.currency
            except Exception:
                cache["fast"][symbol] = None
                continue
            if not price or not raw_currency:
                cache["fast"][symbol] = None
                continue
            currency, divisor = normalize_currency(raw_currency)
            cache["fast"][symbol] = {
                "currency": currency,
                "raw_currency": raw_currency,
                "price": price / divisor,
            }
        _save_cache(cache)
        if start + BATCH < len(pending):
            time.sleep(PAUSE)

    return {s: cache["fast"][s] for s in symbols if cache["fast"].get(s)}


def enrich(symbols: list[str], cache: dict) -> dict[str, dict]:
    """Pasada 2: nombre, bolsa, sector y tipo. Cara: solo los que existen."""
    import yfinance as yf

    pending = [s for s in symbols if s not in cache["info"]]
    for index, symbol in enumerate(pending, 1):
        if index % 25 == 0:
            print(f"  enriqueciendo {index}/{len(pending)}...")
            _save_cache(cache)
            time.sleep(PAUSE)
        try:
            info = yf.Ticker(symbol).info
        except Exception:
            cache["info"][symbol] = None
            continue
        if not info or len(info) <= 1:
            cache["info"][symbol] = None
            continue
        cache["info"][symbol] = {
            "name": info.get("longName") or info.get("shortName"),
            "exchange": info.get("exchange"),
            "sector": info.get("sector"),
            "quote_type": (info.get("quoteType") or "").upper() or None,
            "country": info.get("country"),
        }
    _save_cache(cache)
    return {s: cache["info"].get(s) or {} for s in symbols}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    candidates = all_candidates()
    print(f"{len(candidates)} candidatos en {len({g for _, g, _ in candidates})} grupos")

    cache = _load_cache()
    symbols = [s for s, _, _ in candidates]

    print("\nPasada 1: existencia y divisa")
    alive = verify_existence(symbols, cache)
    dead = [s for s in symbols if s not in alive]
    if dead:
        print(f"  {len(dead)} sin respuesta, reintentando: {' '.join(dead)}")
        alive = verify_existence(symbols, cache, retry_failures=True)
        dead = [s for s in symbols if s not in alive]
    print(f"  {len(alive)} verificados, {len(dead)} descartados")
    if dead:
        print(f"  descartados tras reintento: {' '.join(dead)}")

    print("\nPasada 2: metadatos")
    details = enrich(list(alive), cache)

    assets = []
    for symbol, group, expected_type in candidates:
        fast = alive.get(symbol)
        if not fast:
            continue
        info = details.get(symbol) or {}
        quote_type = info.get("quote_type")
        # Se re-normaliza desde la divisa CRUDA en vez de confiar en la que se
        # guardó en caché: si la tabla de subunidades gana una entrada -pasó
        # con "USX"-, el catálogo se corrige sin volver a salir a la red.
        currency, _ = normalize_currency(fast.get("raw_currency"))
        assets.append(
            {
                "symbol": symbol,
                "name": info.get("name"),
                # El tipo lo manda Yahoo cuando lo sabe; el del grupo es solo
                # la expectativa con la que se escribió la lista.
                "asset_type": QUOTE_TYPES.get(quote_type, expected_type),
                # El sector NO se inventa: se copia el de Yahoo o queda nulo y
                # el pipeline lo rellena. Los cubos de exposición ya tratan la
                # ausencia sin penalizar (ver services/exposure.py).
                "sector": info.get("sector"),
                "currency": currency or fast["currency"],
                "exchange": info.get("exchange"),
                "group": group,
                # Pertenencia al universo de ingesta declarada AQUÍ, junto al
                # activo. Mantenerla en una lista aparte (la vieja
                # DEFAULT_UNIVERSE) invitaba a que las dos se desincronizaran:
                # un símbolo del universo que no estuviera en el catálogo nacía
                # sin divisa ni tipo, es decir como USD/STOCK por defecto.
                "universe": in_universe(group, currency, symbol),
            }
        )

    assets.sort(key=lambda a: (a["group"], a["symbol"]))

    universe_count = sum(1 for a in assets if a["universe"])
    by_currency: dict[str, int] = {}
    by_group: dict[str, int] = {}
    for asset in assets:
        by_currency[asset["currency"]] = by_currency.get(asset["currency"], 0) + 1
        by_group[asset["group"]] = by_group.get(asset["group"], 0) + 1

    print(f"\n{len(assets)} activos verificados, {universe_count} en el universo")
    print("  por grupo:   " + ", ".join(f"{k}={v}" for k, v in sorted(by_group.items())))
    print("  por divisa:  " + ", ".join(
        f"{k}={v}" for k, v in sorted(by_currency.items(), key=lambda x: -x[1])
    ))

    if args.dry_run:
        print("\n--dry-run: no se ha escrito nada")
        return 0

    payload = {
        "_comment": (
            "Catálogo semilla para BUSCAR, no para ingestar. Cada símbolo está "
            "verificado contra Yahoo (precio y divisa) por scripts/build_catalog.py; "
            "regenerar con `python scripts/build_catalog.py`. El sector va en null "
            "salvo donde Yahoo lo da: inventar clasificaciones sería fabricar datos. "
            "La divisa SÍ se declara porque un activo internacional sembrado como USD "
            "se valoraría con el tipo de cambio equivocado hasta el primer refresco."
        ),
        "assets": assets,
    }
    CATALOG_PATH.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nescrito {CATALOG_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
