"""Punto único de importación de todos los modelos.

Alembic autogenerate solo detecta los modelos que estén importados cuando se
evalúa ``Base.metadata``. Centralizarlo aquí evita migraciones que "olvidan"
tablas silenciosamente.
"""

from __future__ import annotations

from app.db.base import Base
from app.models.allocation import AllocationTarget
from app.models.asset import Asset
from app.models.cdt import CdtOffer
from app.models.fixed_income import FixedIncomeTerms
from app.models.fund import FundProfile
from app.models.fundamentals import FundamentalSnapshot
from app.models.fx import FxRateDaily
from app.models.journal import JournalEntry
from app.models.market import AssetQuote, PriceHistory
from app.models.portfolio import Portfolio
from app.models.reference import ReferenceRate
from app.models.scorecard import RankingSnapshot
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
    "JournalEntry",
    "AllocationTarget",
    "RankingSnapshot",
    "ReferenceRate",
    "FixedIncomeTerms",
    "FundProfile",
    "CdtOffer",
]
