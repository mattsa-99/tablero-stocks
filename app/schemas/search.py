from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AssetType


class SymbolSuggestion(BaseModel):
    """Un resultado del autocompletado de símbolos."""

    model_config = ConfigDict(extra="forbid")

    symbol: str
    name: str | None
    exchange: str | None
    asset_type: AssetType

    # None cuando la sugerencia viene del buscador de Yahoo, que no devuelve
    # estos campos. Se rellenan al resolver el símbolo seleccionado.
    currency: str | None
    sector: str | None

    # Precio de referencia. None cuando no hay cotización: se deja vacío en
    # lugar de inventar un número, porque un precio equivocado en el coste base
    # queda congelado para siempre.
    price: float | None = Field(default=None, description="Último precio conocido")
    price_as_of: dt.datetime | None = None
    price_is_stale: bool = Field(
        default=False,
        description="El precio es viejo o su último refresco falló: no presentarlo como actual",
    )

    source: str = Field(description="catalog | provider")
    in_catalog: bool = Field(description="Ya existe como activo en la base local")
    in_universe: bool = Field(description="Se sincroniza a diario")
    is_held: bool = Field(description="Tiene operaciones registradas en algún portafolio")


class SymbolSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    results: list[SymbolSuggestion]
    warnings: list[str] = Field(default_factory=list)
