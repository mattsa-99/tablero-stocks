"""Cómo le fue a cada calificación desde una foto del ranking.

NO ES UN BACKTEST. Un backtest recorre el pasado; esto espera al futuro. Sirve
para detectar lo CONTRARIO de lo esperado -que las notas bajas rindan más que
las altas-, que es la clase de error que una heurística sin validar comete sin
avisar. Nunca para confirmar que funciona.

    python scripts/scorecard.py                 # la foto más antigua
    python scripts/scorecard.py --desde 2026-09-20
    python scripts/scorecard.py --listar
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

os.environ.setdefault("TABLERO_ENABLE_BACKGROUND_REFRESH", "false")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models import Portfolio, RankingSnapshot  # noqa: E402
from app.services import scorecard as scorecard_service  # noqa: E402
from app.services.asset_class import CLASS_LABEL, AssetClass  # noqa: E402
from app.services.grading import GRADE_LABEL, Grade  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portafolio", type=int, default=None)
    parser.add_argument("--desde", type=dt.date.fromisoformat, default=None)
    parser.add_argument("--listar", action="store_true", help="solo lista las fotos")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        portfolio = (
            db.get(Portfolio, args.portafolio)
            if args.portafolio
            else db.scalars(select(Portfolio).order_by(Portfolio.id)).first()
        )
        if portfolio is None:
            print("No hay ningún portafolio.", file=sys.stderr)
            return 1

        fotos = db.execute(
            select(RankingSnapshot.captured_on, func.count())
            .where(RankingSnapshot.portfolio_id == portfolio.id)
            .group_by(RankingSnapshot.captured_on)
            .order_by(RankingSnapshot.captured_on)
        ).all()

        if not fotos:
            print(
                "Todavía no hay ninguna foto del ranking.\n"
                "Se toman solas tras cada sincronización completa, como mucho una\n"
                "por semana. La primera comparación útil llega a los seis meses."
            )
            return 0

        print(f"Fotos disponibles (cartera {portfolio.id} «{portfolio.name}»):")
        for fecha, n in fotos:
            print(f"   {fecha}  {n} filas")
        if args.listar:
            return 0

        desde = args.desde or fotos[0][0]
        tarjeta = scorecard_service.evaluate(db, portfolio, captured_on=desde)

        print(
            f"\nDesde {tarjeta.captured_on} hasta {tarjeta.evaluated_on} "
            f"({tarjeta.horizon_days} días)"
        )
        for aviso in tarjeta.warnings:
            print(f"   AVISO: {aviso}")
        if not tarjeta.outcomes:
            return 0

        print(
            f"\n{'clase':20} {'señales':20} {'n':>4} {'rend.':>8} {'clase':>8} "
            f"{'exceso':>8} {'caída':>8}"
        )
        clase_actual = None
        for r in tarjeta.outcomes:
            etiqueta_clase = CLASS_LABEL.get(AssetClass(r.asset_class), r.asset_class)
            etiqueta_nota = GRADE_LABEL.get(Grade(r.grade), r.grade)
            if r.asset_class != clase_actual:
                print()
                clase_actual = r.asset_class
            caida = "—" if r.median_drawdown_pct is None else f"{r.median_drawdown_pct:7.1f}%"
            print(
                f"{etiqueta_clase:20} {etiqueta_nota:20} {r.count:4} "
                f"{r.median_return_pct:7.1f}% {r.class_median_return_pct:7.1f}% "
                f"{r.excess_vs_class_pct:+7.1f}% {caida:>8}"
            )

        print(
            "\nTodas las cifras son MEDIANAS y ninguna es concluyente: con cinco\n"
            "calificaciones, cinco clases y unos cientos de activos, el ruido\n"
            "domina. Lo que sí importa es el signo de «exceso»: si las notas\n"
            "altas salen sistemáticamente por debajo de su clase, la heurística\n"
            "está midiendo al revés."
        )
        return 0
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
