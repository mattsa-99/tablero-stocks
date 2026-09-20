"""Imprime la ficha de compra de un símbolo en markdown.

    python scripts/ficha.py AAPL
    python scripts/ficha.py KO --portfolio 1
    python scripts/ficha.py PBR --db /ruta/copia.db

SOLO LEE. No llama a Yahoo ni escribe en la base: trabaja con lo último que el
tablero haya guardado (la ficha declara de cuándo es cada dato). Sirve para que
Claude analice una empresa con exactamente los mismos datos y banderas que ve la
interfaz.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("symbol")
    parser.add_argument("--portfolio", type=int, help="id del portafolio (por defecto, el primero)")
    parser.add_argument("--db", help="ruta a un .db alternativo (por defecto, el del tablero)")
    args = parser.parse_args()

    # Debe fijarse ANTES de importar `app`: el motor se crea al importar.
    if args.db:
        os.environ["TABLERO_DATABASE_URL"] = f"sqlite:///{Path(args.db).resolve()}"
    os.environ["TABLERO_ENABLE_BACKGROUND_REFRESH"] = "false"

    from app.core.exceptions import NotFoundError
    from app.db.session import SessionLocal
    from app.repositories import portfolio as portfolio_repo
    from app.services.ficha import build_ficha
    from app.services.ficha_markdown import render_markdown

    with SessionLocal() as db:
        portfolios = portfolio_repo.list_portfolios(db)
        if not portfolios:
            print("No hay portafolios: crea uno en el tablero primero.", file=sys.stderr)
            return 1
        try:
            portfolio = (
                portfolio_repo.get_portfolio(db, args.portfolio)
                if args.portfolio
                else portfolios[0]
            )
            print(render_markdown(build_ficha(db, portfolio, args.symbol)))
        except NotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
