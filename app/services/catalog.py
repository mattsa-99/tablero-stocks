"""Catálogo semilla: activos BUSCABLES, distintos de los activos INGESTADOS.

LA DISTINCIÓN QUE HACE ESCALABLE EL SISTEMA
===========================================
Escalar a cientos o miles de activos choca de frente con el rate limit de
yfinance: cada símbolo cuesta varias llamadas, y una sincronización diaria de
2.000 tickers es imposible. La salida NO es pedir más rápido, sino separar dos
conjuntos que antes eran uno solo:

  CATÁLOGO  (`assets`, `is_universe=False`)
      Miles de filas posibles. Solo alimenta el buscador: nombre, símbolo y
      clase de activo. Cuesta bytes, no llamadas. Nunca se ingesta en bloque.

  UNIVERSO DE INGESTA (`is_universe=True` o con posiciones)
      Decenas. Precios, fundamentales e histórico refrescados a diario.

Un activo pasa del catálogo al universo cuando el usuario lo toca: lo compra,
lo añade explícitamente, o lo consulta y la carga perezosa trae sus datos.

SOBRE EL ALCANCE DEL CATÁLOGO
=============================
Son 194 símbolos verificados, no miles inventados. Un catálogo generado a
ojo estaría lleno de tickers mal escritos o deslistados, y cada uno se
convertiría en un fallo silencioso. El universo REALMENTE buscable no es este
archivo: el buscador consulta Yahoo en vivo y encuentra cualquier cosa que
exista. El catálogo solo hace instantáneos y disponibles sin red los comunes.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Asset, AssetType

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "catalog.json"


@lru_cache(maxsize=1)
def load_catalog() -> list[dict]:
    """Lee el catálogo del disco. Cacheado: el archivo no cambia en caliente."""
    if not CATALOG_PATH.exists():
        logger.warning("No hay catálogo semilla en %s", CATALOG_PATH)
        return []
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return payload.get("assets", [])


def catalog_stats() -> dict[str, int]:
    counts: dict[str, int] = {}
    for entry in load_catalog():
        kind = entry.get("asset_type", "OTHER")
        counts[kind] = counts.get(kind, 0) + 1
    counts["TOTAL"] = sum(v for k, v in counts.items() if k != "TOTAL")
    return counts


def universe_symbols_from_catalog() -> list[str]:
    """Símbolos que el catálogo declara como miembros del universo de ingesta.

    La pertenencia vive junto al activo, no en una lista aparte: así el símbolo
    que se ingesta trae ya su divisa y su tipo verificados. Una lista separada
    se desincroniza, y un símbolo del universo ausente del catálogo nacería con
    los valores por defecto (USD y STOCK), que para un valor japonés o un ETF
    de bonos es sencillamente falso.
    """
    return [e["symbol"] for e in load_catalog() if e.get("universe")]


def seed_catalog(db: Session, *, promote_to_universe: bool = False) -> dict[str, int]:
    """Carga el catálogo en `assets`. Idempotente.

    Los activos entran como CATÁLOGO (`is_universe=False`) salvo que la entrada
    se declare `"universe": true`: buscables, no ingestados. Promoverlos TODOS
    en bloque convertiría la sincronización diaria en cientos de llamadas y
    agotaría el rate limit el primer día; por eso `promote_to_universe` sigue
    siendo opt-in y la pertenencia se declara activo por activo.

    A un activo que ya existe solo se le rellenan los huecos. Nunca se
    sobrescribe lo que vino del proveedor con lo de la semilla: el dato de
    Yahoo es más fiable y más reciente que este archivo. Eso incluye la
    divisa, que además `refresh_metadata` normaliza (GBp -> GBP) mientras que
    la semilla solo declara una expectativa.

    ASIMETRÍA DELIBERADA: sembrar PROMUEVE al universo, nunca degrada. Si el
    catálogo deja de declarar un símbolo, este NO se retira automáticamente.
    Parece un descuido y es lo contrario: el usuario puede añadir símbolos a
    mano con `POST /api/market-data/universe/{symbol}`, y como la siembra corre
    en CADA arranque, degradar aquí desharía esa decisión en silencio una y
    otra vez. No se puede distinguir "sobra respecto al catálogo" de "lo pidió
    el usuario" sin guardar la procedencia, y ante la duda se conserva.

    La consecuencia es que reducir el universo en el catálogo exige una
    retirada explícita (`universe.set_membership(..., member=False)`), que
    tampoco borra nada: el activo y su histórico se conservan.
    """
    entries = load_catalog()
    if not entries:
        return {"created": 0, "enriched": 0, "skipped": 0}

    symbols = [e["symbol"] for e in entries]
    existing = {
        asset.symbol: asset
        for asset in db.scalars(select(Asset).where(Asset.symbol.in_(symbols))).all()
    }

    created = enriched = skipped = 0
    for entry in entries:
        symbol = entry["symbol"]
        asset = existing.get(symbol)

        if asset is None:
            db.add(
                Asset(
                    symbol=symbol,
                    name=entry.get("name"),
                    sector=entry.get("sector"),
                    asset_type=AssetType(entry.get("asset_type", "STOCK")),
                    # La divisa viene del catálogo. Antes se fijaba a USD, lo
                    # que con un catálogo internacional nace mal: un valor
                    # alemán entraría como USD y se valoraría con el tipo
                    # equivocado hasta el primer refresco de metadatos, con la
                    # ventana suficiente para registrar una compra cuyo
                    # `fx_rate_to_base` ya queda congelado y erróneo.
                    currency=(entry.get("currency") or "USD").upper(),
                    exchange=entry.get("exchange"),
                    is_universe=promote_to_universe or bool(entry.get("universe")),
                )
            )
            created += 1
            continue

        touched = False
        if not asset.name and entry.get("name"):
            asset.name = entry["name"]
            touched = True
        if not asset.sector and entry.get("sector"):
            asset.sector = entry["sector"]
            touched = True
        if not asset.exchange and entry.get("exchange"):
            asset.exchange = entry["exchange"]
            touched = True
        if (promote_to_universe or entry.get("universe")) and not asset.is_universe:
            asset.is_universe = True
            touched = True

        enriched += touched
        skipped += not touched

    db.commit()
    logger.info(
        "Catálogo sembrado: %d nuevos, %d enriquecidos, %d sin cambios",
        created, enriched, skipped,
    )
    return {"created": created, "enriched": enriched, "skipped": skipped}


def catalog_size(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Asset)) or 0
