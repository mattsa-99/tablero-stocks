"""API del diario de decisiones y la lista de vigilancia."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.core.exceptions import ProviderError
from app.models.enums import JournalKind
from app.repositories import portfolio as portfolio_repo
from app.routers.dependencies import DbSession, ProviderDep
from app.schemas.journal import (
    JournalCounts,
    JournalCreate,
    JournalEntryRead,
    JournalListResponse,
    JournalReview,
    JournalUpdate,
)
from app.services import ingestion as ingestion_service
from app.services import journal as journal_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/journal", tags=["journal"])


def _respond(db, entry) -> JournalEntryRead:
    portfolio = portfolio_repo.get_portfolio(db, entry.portfolio_id)
    return journal_service.read_entry(db, entry, portfolio)


@router.get("", response_model=JournalListResponse)
def list_journal(
    db: DbSession,
    portfolio_id: Annotated[int, Query()],
    include_archived: Annotated[bool, Query(description="Incluir las cerradas")] = False,
    kind: Annotated[JournalKind | None, Query()] = None,
):
    """Tus decisiones anotadas, con lo que pide atención primero."""
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    return journal_service.list_entries(
        db, portfolio, include_archived=include_archived, kind=kind
    )


@router.get("/summary", response_model=JournalCounts)
def journal_summary(db: DbSession, portfolio_id: Annotated[int, Query()]):
    """Contadores baratos para el aviso de la barra de navegación."""
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    return journal_service.summary(db, portfolio)


@router.post("", response_model=JournalEntryRead, status_code=status.HTTP_201_CREATED)
def create_journal_entry(
    payload: JournalCreate,
    db: DbSession,
    provider: ProviderDep,
    portfolio_id: Annotated[int, Query()],
):
    """Anota una decisión. NO crea ninguna transacción: el ledger no se toca."""
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    asset = journal_service.get_asset(db, payload.symbol)

    # Sin datos no hay foto que guardar. Se traen ahora si faltan; si el
    # proveedor falla, la entrada se crea igual, solo que sin foto.
    try:
        ingestion_service.ensure_data_for(db, provider, [asset])
    except ProviderError as exc:
        logger.warning("Diario: no se pudieron traer datos de %s: %s", asset.symbol, exc)

    entry = journal_service.create_entry(db, portfolio, payload)
    return _respond(db, entry)


@router.patch("/{entry_id}", response_model=JournalEntryRead)
def update_journal_entry(entry_id: int, payload: JournalUpdate, db: DbSession):
    entry = journal_service.update_entry(db, entry_id, payload)
    return _respond(db, entry)


@router.post("/{entry_id}/review", response_model=JournalEntryRead)
def review_journal_entry(entry_id: int, payload: JournalReview, db: DbSession):
    """Marca como revisada: guarda qué viste hoy y fija la próxima revisión."""
    entry = journal_service.review_entry(db, entry_id, payload)
    return _respond(db, entry)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_journal_entry(entry_id: int, db: DbSession):
    journal_service.delete_entry(db, entry_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
