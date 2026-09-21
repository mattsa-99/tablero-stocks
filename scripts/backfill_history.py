"""Relleno PROFUNDO del histórico de precios: trae el pasado que falta.

La sincronización diaria solo pide barras nuevas. Este script pide las
ANTIGUAS, que es lo que hace falta cuando se amplía `price_history_days`.

    python scripts/backfill_history.py                 # todo el universo
    python scripts/backfill_history.py --simbolos AAPL,KO
    python scripts/backfill_history.py --lote 25 --pausa 2

No cuesta más LLAMADAS que una sincronización normal -`fetch_history` es un
único `yf.download` por lote, así que la profundidad solo cambia el tamaño de
la respuesta-, pero sí tarda: son 494 símbolos en lotes con pausa.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("TABLERO_ENABLE_BACKGROUND_REFRESH", "false")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models import Asset, PriceHistory  # noqa: E402
from app.providers.yfinance_client import YFinanceClient  # noqa: E402
from app.services import universe  # noqa: E402
from app.services.market_data import MarketDataService  # noqa: E402


def _profundidad(db) -> tuple[int, dt.date | None, dt.date | None]:
    filas, primera, ultima = db.execute(
        select(func.count(), func.min(PriceHistory.date), func.max(PriceHistory.date))
    ).one()
    return filas, primera, ultima


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simbolos", default="", help="lista separada por comas")
    parser.add_argument("--lote", type=int, default=settings.ingestion_batch_size)
    parser.add_argument(
        "--pausa", type=float, default=settings.ingestion_batch_delay_seconds
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if args.simbolos:
            pedidos = [s.strip().upper() for s in args.simbolos.split(",") if s.strip()]
            assets = db.scalars(select(Asset).where(Asset.symbol.in_(pedidos))).all()
        else:
            assets = universe.get_ingestion_universe(db)
        assets = list(assets)

        antes = _profundidad(db)
        print(
            f"Antes: {antes[0]:,} barras, {antes[1]} -> {antes[2]}\n"
            f"Objetivo: {settings.price_history_days} días de profundidad\n"
            f"{len(assets)} símbolos en lotes de {args.lote} con {args.pausa}s de pausa"
        )

        service = MarketDataService(db, YFinanceClient())
        escritas = 0
        comienzo = time.monotonic()
        for numero, inicio in enumerate(range(0, len(assets), args.lote), start=1):
            lote = assets[inicio : inicio + args.lote]
            report = service.refresh_price_history(lote, deep=True)
            escritas += report.bars_written
            print(
                f"  lote {numero}: {report.bars_written:>7,} barras"
                f"  ({inicio + len(lote)}/{len(assets)})"
                + (f"  avisos: {len(report.warnings)}" if report.warnings else "")
            )
            if args.pausa and inicio + args.lote < len(assets):
                time.sleep(args.pausa)

        despues = _profundidad(db)
        print(
            f"\nDespués: {despues[0]:,} barras, {despues[1]} -> {despues[2]}\n"
            f"Escritas: {escritas:,} en {time.monotonic() - comienzo:.0f}s"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
