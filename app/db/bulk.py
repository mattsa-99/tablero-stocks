"""Inserción masiva idempotente, portable entre SQLite y PostgreSQL.

`ON CONFLICT DO NOTHING` existe en ambos motores pero se construye desde
módulos de dialecto distintos. Este helper elige el correcto según la conexión,
para que los servicios no importen `sqlalchemy.dialects.sqlite` y la migración
a PostgreSQL no obligue a tocarlos.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Table
from sqlalchemy.orm import Session


def insert_ignore_duplicates(
    db: Session,
    table: Table,
    rows: Sequence[dict[str, Any]],
    *,
    index_elements: Sequence[str],
) -> int:
    """Inserta `rows` ignorando las que violen la restricción indicada.

    Devuelve el número de filas escritas cuando el driver lo informa; si no
    (rowcount con executemany no es fiable en todos), devuelve el tamaño del
    lote, que es su cota superior.

    Dejar que la base decida el conflicto -en vez de consultar antes qué existe-
    ahorra una consulta por lote y elimina la carrera entre la comprobación y
    la escritura.
    """
    if not rows:
        return 0

    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        statement = pg_insert(table).on_conflict_do_nothing(index_elements=index_elements)
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        statement = sqlite_insert(table).on_conflict_do_nothing(
            index_elements=index_elements
        )
    else:  # pragma: no cover - ningún otro motor está soportado hoy
        raise NotImplementedError(
            f"Inserción masiva idempotente no implementada para el dialecto {dialect!r}"
        )

    result = db.execute(statement, list(rows))
    return result.rowcount if result.rowcount and result.rowcount >= 0 else len(rows)
