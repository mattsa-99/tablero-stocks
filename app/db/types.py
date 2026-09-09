"""Tipos de columna a medida.

Este módulo es la razón por la que el P&L de este sistema es exacto. Cualquier
cambio aquí afecta a la corrección de todos los cálculos financieros.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class ExactNumeric(TypeDecorator):
    """DECIMAL exacto, también en SQLite.

    SQLite no tiene tipo DECIMAL nativo: su dialecto pasa los valores por
    ``float`` y los reconvierte, lo que introduce error de representación
    binaria en importes monetarios (``0.1 + 0.2 != 0.3``). Ese error se acumula
    en el coste medio, que se recalcula tras cada compra, y acaba produciendo
    un P&L desviado en céntimos que el usuario ve y no puede explicar.

    Aquí el valor se almacena como TEXT normalizado y se devuelve siempre como
    ``Decimal``, de modo que la aritmética es exacta. En PostgreSQL se usa
    NUMERIC nativo, sin ninguna capa intermedia.

    LIMITACIÓN CONSCIENTE: en SQLite estas columnas NO deben usarse en
    ``SUM()`` ni ``ORDER BY`` de SQL, porque la comparación sería
    lexicográfica ('9' > '10'). Toda la agregación financiera se hace en
    Python, que es donde vive el replay del ledger. La limitación desaparece
    al migrar a PostgreSQL.
    """

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = 20, scale: int = 8, **kw) -> None:
        self.precision = precision
        self.scale = scale
        super().__init__(precision=precision, scale=scale, asdecimal=True, **kw)

    def load_dialect_impl(self, dialect: Dialect):
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(48))
        return dialect.type_descriptor(
            Numeric(self.precision, self.scale, asdecimal=True)
        )

    def process_bind_param(self, value, dialect: Dialect):
        if value is None:
            return None
        # str() y no Decimal(value) directamente: Decimal(0.1) arrastra la
        # representación binaria completa (0.1000000000000000055511151231...).
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
        if dialect.name == "sqlite":
            return format(dec, "f")
        return dec

    def process_result_value(self, value, dialect: Dialect):
        if value is None:
            return None
        return value if isinstance(value, Decimal) else Decimal(str(value))


class UtcDateTime(TypeDecorator):
    """TIMESTAMP siempre timezone-aware en UTC.

    SQLite no almacena zona horaria. Normalizamos a UTC al escribir y
    re-etiquetamos como UTC al leer, para que el código de dominio nunca vea
    un datetime naive. Escribir uno naive es un error explícito: el usuario
    registra operaciones en hora local (Colombia, -05:00) y perder ese offset
    desplazaría las transacciones cinco horas.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect):
        return dialect.type_descriptor(DateTime(timezone=dialect.name != "sqlite"))

    def process_bind_param(self, value, dialect: Dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(
                "Se requiere un datetime timezone-aware (ej. 2026-08-30T10:00:00-05:00)"
            )
        return value.astimezone(dt.UTC)

    def process_result_value(self, value, dialect: Dialect):
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


# Aliases semánticos: el tipo comunica la intención y la escala.
#
# Money(20, 8) deja 12 dígitos enteros -> hasta ~999.999 millones de COP por
# transacción, holgado para el contexto de uso (10 millones COP ~ 2.500 USD).
Money = ExactNumeric(20, 8)
Quantity = ExactNumeric(28, 10)  # admite fraccionarios y cripto
FxRate = ExactNumeric(24, 12)
BigMoney = ExactNumeric(24, 2)  # capitalización bursátil
