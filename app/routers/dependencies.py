"""Dependencias compartidas por los routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

from app.db.session import SessionLocal, get_db
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


def get_session_factory() -> sessionmaker:
    """Fábrica de sesiones para trabajo que SOBREVIVE a la petición.

    Una tarea de fondo no puede usar la sesión de la petición: FastAPI la
    cierra al enviar la respuesta, que es justo cuando la tarea empieza.

    Y es una dependencia, no un `SessionLocal` importado, por la misma razón
    que el proveedor: los tests sustituyen `get_db` por una base temporal, así
    que una tarea que se construyera la suya escribiría en la base REAL
    mientras corre la suite.
    """
    return SessionLocal


SessionFactory = Annotated[sessionmaker, Depends(get_session_factory)]


def get_market_service(db: DbSession, provider: ProviderDep) -> MarketDataService:
    return MarketDataService(db, provider)


MarketService = Annotated[MarketDataService, Depends(get_market_service)]
