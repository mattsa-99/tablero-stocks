"""Punto único de importación de todos los modelos.

Alembic autogenerate solo detecta los modelos que estén importados cuando se
evalúa ``Base.metadata``. Centralizarlo aquí evita migraciones que "olvidan"
tablas silenciosamente.
"""

from __future__ import annotations

from app.db.base import Base
from app.models.asset import Asset
from app.models.fundamentals import FundamentalSnapshot
from app.models.fx import FxRateDaily
from app.models.market import AssetQuote, PriceHistory
from app.models.portfolio import Portfolio
from app.models.sync import DataSyncState
from app.models.transaction import Transaction

__all__ = [
    "Base",
    "Portfolio",
    "Asset",
    "Transaction",
    "PriceHistory",
    "AssetQuote",
    "FundamentalSnapshot",
    "FxRateDaily",
    "DataSyncState",
]
