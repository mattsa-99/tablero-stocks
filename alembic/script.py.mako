"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
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
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
