"""Vuelve a aplicar los filtros de `data_quality` a los snapshots ya guardados.

SIN RED. El dato crudo del proveedor está en `fundamental_snapshots.raw`, así
que cuando cambian las reglas de descarte no hace falta redescargar nada: se
reevalúa lo guardado.

Existe porque el filtro corre en la frontera, al escribir. Sin esto, una regla
nueva solo se aplicaría a los datos futuros y el ranking seguiría apoyado en
los múltiplos falsos que ya están en la base hasta la siguiente sincronización
completa.

    python scripts/resanitise_fundamentals.py --ensayo   # solo informa
    python scripts/resanitise_fundamentals.py            # escribe
"""

from __future__ import annotations

import argparse
import collections
import os
import sys
from pathlib import Path

os.environ.setdefault("TABLERO_ENABLE_BACKGROUND_REFRESH", "false")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models import Asset, FundamentalSnapshot  # noqa: E402
from app.services import asset_class, data_quality  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ensayo", action="store_true", help="no escribe nada")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        activos = {a.id: a for a in db.scalars(select(Asset)).all()}
        snapshots = db.scalars(select(FundamentalSnapshot)).all()

        motivos: collections.Counter[str] = collections.Counter()
        por_simbolo: dict[str, set[str]] = collections.defaultdict(set)
        tocados = 0

        for snapshot in snapshots:
            asset = activos.get(snapshot.asset_id)
            if asset is None:
                continue
            clasificacion = asset_class.classify(asset)
            revisado = data_quality.sanitise_multiples(
                {
                    nombre: getattr(snapshot, nombre)
                    for nombre in (
                        *data_quality.VALUATION_MULTIPLES,
                        *data_quality.EARNINGS_INPUTS,
                    )
                },
                quote_currency=asset.currency,
                financial_currency=(snapshot.raw or {}).get("financialCurrency"),
                asset_class=clasificacion.asset_class,
                is_assumed=clasificacion.is_assumed,
            )
            if not revisado.has_drops:
                continue
            tocados += 1
            for ratio, motivo in revisado.dropped.items():
                motivos[motivo.split(":")[0]] += 1
                por_simbolo[asset.symbol].add(ratio)
            for nombre, valor in revisado.values.items():
                setattr(snapshot, nombre, valor)

        print(f"snapshots revisados: {len(snapshots)}   con descartes: {tocados}")
        print(f"activos afectados:   {len(por_simbolo)}")
        for motivo, n in motivos.most_common():
            print(f"   {n:5}  {motivo}")
        muestra = sorted(por_simbolo)[:20]
        print(f"   ejemplos: {', '.join(muestra)}")

        if args.ensayo:
            db.rollback()
            print("\nEnsayo: no se escribió nada.")
        else:
            db.commit()
            print("\nEscrito.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
