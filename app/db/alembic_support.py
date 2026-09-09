"""Soporte compartido entre alembic/env.py y los tests de migraciones.

Vive en el paquete de la app, y no dentro de ``alembic/env.py``, para que los
tests puedan importar exactamente la misma lógica de comparación. Si estuviera
duplicada, el test de deriva podría pasar mientras el autogenerate real falla.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint

from app.db.registry import Base


def type_bound_check_names() -> frozenset[str]:
    """Nombres de los CHECK generados automáticamente por un tipo Enum.

    SQLAlchemy los marca con ``_type_bound=True``. Alembic los excluye del lado
    del MODELO pero sí los ve al reflejar la base de datos, y esa asimetría
    hace que autogenerate los reporte como "removed" en cada ejecución. La
    migración espuria resultante los BORRARÍA, dejando las columnas de enum sin
    validación de dominio.

    Se deriva de la metadata, así que añadir un enum nuevo no reintroduce el
    problema.
    """
    return frozenset(
        str(constraint.name)
        for table in Base.metadata.tables.values()
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
        and getattr(constraint, "_type_bound", False)
        and constraint.name is not None
    )


TYPE_BOUND_CHECKS = type_bound_check_names()


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Filtro de autogenerate. Ver ``type_bound_check_names``."""
    return not (type_ == "check_constraint" and name in TYPE_BOUND_CHECKS)


COMPARE_OPTIONS = {
    "include_object": include_object,
    "compare_type": True,
    "compare_server_default": True,
}
