"""Dependencias compartidas por los routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.providers.base import MarketProvider
from app.providers.yfinance_client import YFinanceClient
from app.services.market_data import MarketDataService

DbSession = Annotated[Session, Depends(get_db)]


def get_provider() -> MarketProvider:
    """Proveedor de datos de mercado.

    Es una dependencia y no un import directo para poder sustituirlo por un
    doble en los tests con `app.dependency_overrides`, sin tocar red.
    """
    return YFinanceClient()


ProviderDep = Annotated[MarketProvider, Depends(get_provider)]


def get_market_service(db: DbSession, provider: ProviderDep) -> MarketDataService:
    return MarketDataService(db, provider)


MarketService = Annotated[MarketDataService, Depends(get_market_service)]
