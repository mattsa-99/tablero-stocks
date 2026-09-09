from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9._\-]{0,19}$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _normalize_symbol(value: str) -> str:
    value = value.strip().upper()
    if not _SYMBOL_RE.match(value):
        raise ValueError(
            "Símbolo inválido: se esperan 1-20 caracteres alfanuméricos, '.', '_' o '-' "
            "(ej. AAPL, BRK-B, VWCE.DE)"
        )
    return value


def _normalize_currency(value: str) -> str:
    value = value.strip().upper()
    if not _CURRENCY_RE.match(value):
        raise ValueError("Divisa inválida: se espera un código ISO 4217 de 3 letras (ej. COP, USD)")
    return value


Symbol = Annotated[str, AfterValidator(_normalize_symbol)]
CurrencyCode = Annotated[str, AfterValidator(_normalize_currency)]

PositiveQuantity = Annotated[Decimal, Field(gt=0, max_digits=28, decimal_places=10)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=8)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=8)]
PositiveRate = Annotated[Decimal, Field(gt=0, max_digits=24, decimal_places=12)]


class ORMModel(BaseModel):
    """Base para schemas de lectura mapeados desde el ORM."""

    model_config = ConfigDict(from_attributes=True)


class StrictModel(BaseModel):
    """Base para schemas de entrada.

    ``extra="forbid"`` es deliberado: un typo en el nombre de un campo
    (``comission`` en vez de ``fees``) debe ser un 422, no una comisión
    ignorada en silencio que corrompe el coste base para siempre.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
