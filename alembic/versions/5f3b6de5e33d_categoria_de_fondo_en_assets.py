"""categoria de fondo en assets

Revision ID: 5f3b6de5e33d
Revises: ae5068edce82
Create Date: 2026-09-20 23:14:08.865658
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# Autogenerate renderiza las columnas monetarias como app.db.types.ExactNumeric
# y las de fecha como app.db.types.UtcDateTime, pero NO añade este import por
# su cuenta. Sin él, cualquier migración que toque esas columnas falla con
# NameError al ejecutarse.
import app.db.types  # noqa: F401

# El variant JSONB de PostgreSQL se renderiza como
# postgresql.JSONB(astext_type=Text()), con Text sin cualificar.
from sqlalchemy import Text  # noqa: F401


revision: str = '5f3b6de5e33d'
down_revision: str | None = 'ae5068edce82'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fund_category", sa.String(length=60), nullable=True))

    _rellenar_desde_los_snapshots()


def _rellenar_desde_los_snapshots() -> None:
    """Copia la categoría del último `raw` guardado de cada activo.

    No inventa nada ni sale a la red: el dato YA está descargado, dentro del
    JSON crudo de `fundamental_snapshots`. Sin esto, la columna quedaría vacía
    hasta que caducara el TTL de metadatos -treinta días- y durante ese mes la
    clasificación por datos seguiría cayendo en la heurística que viene a
    sustituir.

    Se parsea en Python y no con `json_extract` para no depender del dialecto.
    """
    import json

    conexion = op.get_bind()
    filas = conexion.execute(
        sa.text(
            """
            SELECT f.asset_id, f.raw
            FROM fundamental_snapshots f
            WHERE f.id = (
                SELECT id FROM fundamental_snapshots
                WHERE asset_id = f.asset_id ORDER BY as_of DESC LIMIT 1
            )
            AND f.raw IS NOT NULL
            """
        )
    ).fetchall()

    actualizaciones = []
    for asset_id, crudo in filas:
        try:
            datos = json.loads(crudo) if isinstance(crudo, str) else (crudo or {})
        except (TypeError, ValueError):
            continue
        categoria = (datos or {}).get("category")
        if categoria:
            actualizaciones.append({"aid": asset_id, "cat": str(categoria)[:60]})

    if actualizaciones:
        conexion.execute(
            sa.text("UPDATE assets SET fund_category = :cat WHERE id = :aid"),
            actualizaciones,
        )


def downgrade() -> None:
    with op.batch_alter_table("assets", schema=None) as batch_op:
        batch_op.drop_column("fund_category")
