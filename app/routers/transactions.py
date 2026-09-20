from __future__ import annotations

from fastapi import APIRouter, Query, Response, status

from app.repositories import portfolio as portfolio_repo
from app.routers.dependencies import DbSession
from app.schemas.importing import ImportReport, ImportRequest
from app.schemas.transaction import TransactionCreate, TransactionRead, TransactionUpdate
from app.services import importer as importer_service
from app.services import transactions as transaction_service

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


@router.post("", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
def create_transaction(
    payload: TransactionCreate,
    db: DbSession,
    portfolio_id: int = Query(..., description="Portafolio al que pertenece la operación"),
):
    """Registra una compra, venta, dividendo o movimiento de caja.

    El símbolo se resuelve (o se da de alta) automáticamente. Si las divisas
    difieren y no se indica `fx_rate_to_base`, se toma de `fx_rates` en la
    fecha de la operación; si tampoco hay, la petición se rechaza en vez de
    asumir paridad.
    """
    transaction = transaction_service.create_transaction(db, portfolio_id, payload)
    return transaction_service.to_read(transaction)


@router.get("/import/template")
def import_template():
    """Plantilla CSV. Es el ÚNICO formato aceptado: no se adivinan los de broker."""
    return Response(
        content=importer_service.TEMPLATE,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="plantilla-operaciones.csv"'},
    )


@router.post("/import", response_model=ImportReport)
def import_transactions(
    payload: ImportRequest,
    db: DbSession,
    portfolio_id: int = Query(..., description="Portafolio al que se importa"),
    dry_run: bool = Query(
        True,
        description=(
            "Por defecto SOLO REVISA y no escribe nada. Pasa `false` para importar. "
            "Con un solo error no se importa ninguna fila"
        ),
    ),
):
    """Importa operaciones desde un CSV con la plantilla fija, todo o nada."""
    portfolio = portfolio_repo.get_portfolio(db, portfolio_id)
    return importer_service.import_csv(db, portfolio, payload.csv, dry_run=dry_run)


@router.get("/{transaction_id}", response_model=TransactionRead)
def get_transaction(transaction_id: int, db: DbSession):
    transaction = transaction_service.get_transaction_or_404(db, transaction_id)
    return transaction_service.to_read(transaction)


@router.patch("/{transaction_id}", response_model=TransactionRead)
def update_transaction(transaction_id: int, payload: TransactionUpdate, db: DbSession):
    """Corrige una transacción. `type` y `symbol` son inmutables."""
    transaction = transaction_service.update_transaction(db, transaction_id, payload)
    return transaction_service.to_read(transaction)


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_transaction(transaction_id: int, db: DbSession):
    transaction_service.delete_transaction(db, transaction_id)
