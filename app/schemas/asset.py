from __future__ import annotations

from app.models.enums import AssetType
from app.schemas.common import ORMModel, StrictModel, Symbol


class AssetRead(ORMModel):
    """El activo tal y como lo ve el cliente: por símbolo, nunca por id interno."""

    symbol: str
    name: str | None
    asset_type: AssetType
    currency: str
    exchange: str | None
    sector: str | None
    industry: str | None
    country: str | None
    is_active: bool


class AssetRefreshRequest(StrictModel):
    """Fuerza el refresco de metadatos y fundamentales de un símbolo."""

    symbol: Symbol
    force: bool = False
