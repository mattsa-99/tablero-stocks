"""Universo de seguimiento: los activos que se ingestan a diario.

El universo NO es "todo lo que hay en el catálogo". Es la unión de:

  1. Los miembros declarados (`is_universe`), sembrados desde configuración.
  2. Los activos con posición abierta en cualquier portafolio.

La segunda parte es lo que evita el fallo silencioso más obvio de un pipeline
con lista fija: comprar algo fuera de la lista y quedarse sin precio para
valorarlo. Se posee, luego se ingesta, esté o no en la semilla.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Asset, AssetType, Transaction, TransactionType

logger = logging.getLogger(__name__)

# RESPALDO mínimo, para cuando no hay catálogo en disco. El universo real se
# declara en `app/data/catalog.json` con `"universe": true`, donde cada símbolo
# llega ya verificado y con su divisa; ver `default_symbols()`.
#
# Se conserva pequeño y explicable a propósito: es lo que debe arrancar cuando
# todo lo demás falta, no la lista de trabajo.
#
# Los tipos son heterogéneos a propósito para ejercitar el pipeline: acciones,
# ETFs de índice, sectoriales, renta fija, y materias primas vía futuro
# continuo (GC=F, oro). El motor de oportunidades tratará los que no tengan
# fundamentales como factor de valoración ausente, no como valor cero.
DEFAULT_UNIVERSE: tuple[tuple[str, str], ...] = (
    # Acciones grandes
    ("AAPL", AssetType.STOCK),
    ("MSFT", AssetType.STOCK),
    ("NVDA", AssetType.STOCK),
    ("AMZN", AssetType.STOCK),
    ("GOOGL", AssetType.STOCK),
    ("META", AssetType.STOCK),
    ("JPM", AssetType.STOCK),
    ("JNJ", AssetType.STOCK),
    ("XOM", AssetType.STOCK),
    ("KO", AssetType.STOCK),
    ("PG", AssetType.STOCK),
    ("UNH", AssetType.STOCK),
    ("V", AssetType.STOCK),
    ("HD", AssetType.STOCK),
    ("CAT", AssetType.STOCK),
    # ETFs de índice y sectoriales
    ("SPY", AssetType.ETF),
    ("QQQ", AssetType.ETF),
    ("VTI", AssetType.ETF),
    ("IWM", AssetType.ETF),
    ("XLE", AssetType.ETF),
    ("XLF", AssetType.ETF),
    ("XLV", AssetType.ETF),
    # Renta fija e internacional
    ("TLT", AssetType.ETF),
    ("EEM", AssetType.ETF),
    # Materias primas (futuro continuo)
    ("GC=F", AssetType.OTHER),
    ("CL=F", AssetType.OTHER),
)


def seed_universe(db: Session, symbols: list[str] | None = None) -> int:
    """Marca los símbolos como miembros del universo, creándolos si hace falta.

    Idempotente: se puede llamar en cada arranque sin duplicar nada. Los
    activos nacen sin verificar; el pipeline los enriquece en la primera
    sincronización.
    """
    entries: list[tuple[str, str]] = (
        [(s.strip().upper(), AssetType.STOCK) for s in symbols if s.strip()]
        if symbols is not None
        else list(DEFAULT_UNIVERSE)
    )
    if not entries:
        return 0

    wanted = {symbol for symbol, _ in entries}
    existing = {
        asset.symbol: asset
        for asset in db.scalars(select(Asset).where(Asset.symbol.in_(wanted))).all()
    }

    added = 0
    for symbol, asset_type in entries:
        asset = existing.get(symbol)
        if asset is None:
            db.add(Asset(symbol=symbol, asset_type=asset_type, is_universe=True))
            added += 1
        elif not asset.is_universe:
            asset.is_universe = True
            added += 1

    db.commit()
    if added:
        logger.info("Universo: %d símbolos añadidos o promovidos", added)
    return added


def held_asset_ids(db: Session) -> set[int]:
    """Activos con alguna operación de compraventa registrada.

    Se filtra por tipo en SQL en lugar de reproducir el ledger: aquí no
    interesa la cantidad exacta sino "¿lo he tocado alguna vez?". Un activo que
    se vendió entero se sigue ingestando, que es lo correcto: su histórico
    respalda el P&L realizado y el usuario puede recomprarlo.
    """
    rows = db.scalars(
        select(Transaction.asset_id)
        .where(
            Transaction.asset_id.is_not(None),
            Transaction.type.in_([TransactionType.BUY, TransactionType.SELL]),
        )
        .distinct()
    ).all()
    return set(rows)


def get_ingestion_universe(db: Session) -> list[Asset]:
    """Los activos que el pipeline debe refrescar, ordenados por símbolo."""
    held = held_asset_ids(db)
    stmt = select(Asset).where(
        Asset.is_active.is_(True),
        (Asset.is_universe.is_(True)) | (Asset.id.in_(held) if held else False),
    )
    return list(db.scalars(stmt.order_by(Asset.symbol)).all())


def set_membership(db: Session, symbol: str, member: bool) -> Asset:
    """Añade o retira un símbolo del universo.

    Retirar NUNCA borra el activo ni su histórico: puede tener transacciones, y
    los datos ya descargados siguen siendo válidos. Solo deja de refrescarse.
    """
    symbol = symbol.strip().upper()
    asset = db.scalar(select(Asset).where(Asset.symbol == symbol))
    if asset is None:
        if not member:
            from app.core.exceptions import NotFoundError

            raise NotFoundError(f"El símbolo {symbol} no está en el catálogo")
        asset = Asset(symbol=symbol, is_universe=True)
        db.add(asset)
    else:
        asset.is_universe = member

    db.commit()
    db.refresh(asset)
    return asset


def universe_size(db: Session) -> int:
    return len(get_ingestion_universe(db))


def default_symbols() -> list[str]:
    """Los símbolos del universo, por orden de precedencia.

    1. `universe_symbols` de la configuración, si el usuario la fija.
    2. Lo que el catálogo declare con `"universe": true`. Es la fuente normal:
       esos símbolos vienen verificados contra Yahoo y con su divisa real, que
       para un valor japonés o un ETF de bonos importa.
    3. `DEFAULT_UNIVERSE`, el respaldo mínimo si no hay catálogo en disco.

    Los símbolos del catálogo salen verificados contra Yahoo, así que llegan
    tal cual; `seed_universe` los normaliza igual que a los de configuración.
    """
    configured = settings.universe_symbols
    if configured:
        return [s.strip().upper() for s in configured.split(",") if s.strip()]

    from app.services.catalog import universe_symbols_from_catalog

    from_catalog = universe_symbols_from_catalog()
    if from_catalog:
        return from_catalog
    return [symbol for symbol, _ in DEFAULT_UNIVERSE]
