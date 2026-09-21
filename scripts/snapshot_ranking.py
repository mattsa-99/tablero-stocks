"""Foto del ranking completo, para comparar el antes y el después de un cambio.

SOLO LECTURA: abre la base, puntúa el universo y escribe un CSV. No toca ni
una fila. Existe porque el score es un rango percentil sobre datos vivos: una
vez cambiada la fórmula, el ranking anterior ya no se puede reconstruir.

    python scripts/snapshot_ranking.py --salida foto.csv [--portafolio N]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys
from pathlib import Path

os.environ.setdefault("TABLERO_ENABLE_BACKGROUND_REFRESH", "false")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models import Portfolio  # noqa: E402
from app.services import opportunities, universe  # noqa: E402

COLUMNAS = [
    "rank", "rank_in_grade", "asset_class", "class_size",
    "symbol", "asset_type", "score", "grade", "grade_label",
    "signals_available", "value", "value_available", "momentum",
    "momentum_available", "diversification", "diversification_available",
    "risk", "exposure_bucket", "exposure_is_assumed", "market_region",
    "confidence", "current_price",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", required=True, type=Path)
    parser.add_argument("--portafolio", type=int, default=None)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        portfolio = (
            db.get(Portfolio, args.portafolio)
            if args.portafolio
            else db.scalars(select(Portfolio).order_by(Portfolio.id)).first()
        )
        if portfolio is None:
            print("No hay ningún portafolio en la base.", file=sys.stderr)
            return 1

        assets = universe.get_ingestion_universe(db)
        tipo = {a.symbol: a.asset_type.value for a in assets}
        scored = opportunities._score_universe(db, portfolio, assets)

        args.salida.parent.mkdir(parents=True, exist_ok=True)
        with args.salida.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNAS)
            writer.writeheader()
            for row in scored.rows:
                writer.writerow({
                    "rank": row.rank,
                    "rank_in_grade": row.rank_in_grade,
                    "asset_class": row.asset_class,
                    "class_size": row.class_size,
                    "symbol": row.symbol,
                    "asset_type": tipo.get(row.symbol, ""),
                    "score": row.score,
                    "grade": row.assessment.grade,
                    "grade_label": row.assessment.label,
                    "signals_available": sum(
                        1 for s in row.assessment.signals if s.points is not None
                    ),
                    "value": row.value.score,
                    "value_available": row.value.available,
                    "momentum": row.momentum.score,
                    "momentum_available": row.momentum.available,
                    "diversification": row.diversification.score,
                    "diversification_available": row.diversification.available,
                    "risk": row.risk.score,
                    "exposure_bucket": row.exposure_bucket,
                    "exposure_is_assumed": row.exposure_is_assumed,
                    "market_region": row.market_region,
                    "confidence": row.confidence,
                    "current_price": row.current_price,
                })

        print(
            f"{len(scored.rows)} filas escritas en {args.salida} "
            f"(cartera {portfolio.id} «{portfolio.name}», {dt.date.today().isoformat()})"
        )
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
