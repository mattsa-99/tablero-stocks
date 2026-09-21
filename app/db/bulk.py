"""Escritura masiva idempotente, portable entre SQLite y PostgreSQL.

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


def upsert(
    db: Session,
    table: Table,
    rows: Sequence[dict[str, Any]],
    *,
    index_elements: Sequence[str],
    update_columns: Sequence[str],
) -> int:
    """Inserta `rows` y PISA las columnas indicadas si la fila ya existía.

    La diferencia con `insert_ignore_duplicates` no es de eficiencia sino de
    significado, y elegir mal produce datos sutilmente equivocados. DO NOTHING
    dice "lo primero que llegó manda"; esto dice "lo último que llegó manda".

    El caso que lo motivó: el histórico de tipos de cambio. Una fila de un día
    pasado escrita por el refresco de jornada guarda la cotización VIVA del
    momento en que se pidió -las 2 de la tarde, pongamos-, no el cierre. Para
    valorar un día ya cerrado el canónico es el cierre, así que el histórico
    debe pisar. Medido sobre USD/COP: las filas intradía se desviaban del
    cierre entre 0,1% y 0,9%.
    """
    if not rows:
        return 0

    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        statement = pg_insert(table)
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        statement = sqlite_insert(table)
    else:  # pragma: no cover - ningún otro motor está soportado hoy
        raise NotImplementedError(
            f"Upsert masivo no implementado para el dialecto {dialect!r}"
        )

    statement = statement.on_conflict_do_update(
        index_elements=index_elements,
        set_={column: statement.excluded[column] for column in update_columns},
    )
    result = db.execute(statement, list(rows))
    return result.rowcount if result.rowcount and result.rowcount >= 0 else len(rows)
