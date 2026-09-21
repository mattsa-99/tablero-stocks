"""API de la renta fija directa: TES, CDT y FIC cargados a mano."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.core.exceptions import DuplicateError, NotFoundError
from app.models import Asset, AssetType, FixedIncomeTerms
from app.routers.dependencies import DbSession
from app.schemas.fixed_income import AccrualRead, FixedIncomeCreate, FixedIncomeRead
from app.services import fixed_income as fixed_income_service

router = APIRouter(prefix="/api/fixed-income", tags=["fixed-income"])


def _read(db, asset: Asset, terms: FixedIncomeTerms) -> FixedIncomeRead:
    evaluacion = fixed_income_service.assess(db, asset, terms)
    indices = fixed_income_service.rates_service.latest(db)
    tasa, _ = fixed_income_service.effective_annual_rate(terms, indices)
    devengo = (
        fixed_income_service.accrue(terms, tasa) if tasa is not None else None
    )
    return FixedIncomeRead(
        **vars(evaluacion),
        accrual=AccrualRead(**vars(devengo)) if devengo else None,
    )


@router.get("", response_model=list[FixedIncomeRead])
def list_instruments(db: DbSession) -> list[FixedIncomeRead]:
    """Todos los instrumentos cargados, con su evaluación al día de hoy."""
    activos = db.scalars(
        select(Asset).where(Asset.asset_type == AssetType.FIXED_INCOME)
    ).all()
    salida = []
    for asset in activos:
        terms = fixed_income_service.get_terms(db, asset)
        if terms is not None:
            salida.append(_read(db, asset, terms))
    return salida


@router.post("", response_model=FixedIncomeRead, status_code=status.HTTP_201_CREATED)
def create_instrument(db: DbSession, payload: FixedIncomeCreate) -> FixedIncomeRead:
    """Registra un TES, un CDT o una FIC.

    CREA EL ACTIVO Y SUS CONDICIONES, no la compra. La compra se registra como
    cualquier otra transacción, con `quantity` = el capital y `price` = 1: así
    el ledger sigue siendo la única fuente de verdad y no hace falta ni un caso
    especial en el replay.
    """
    simbolo = payload.symbol.strip().upper()
    if db.scalar(select(Asset).where(Asset.symbol == simbolo)) is not None:
        raise DuplicateError(f"Ya existe un activo con el símbolo {simbolo}.")

    asset = Asset(
        symbol=simbolo,
        name=payload.name or f"{payload.issuer} · vence {payload.matures_on}",
        asset_type=AssetType.FIXED_INCOME,
        currency=payload.currency.upper(),
        # NO entra al universo de ingesta: no hay nada que pedirle a ningún
        # proveedor y meterlo gastaría una llamada por sincronización para
        # recibir siempre el mismo 404.
        is_universe=False,
        is_active=True,
    )
    db.add(asset)
    db.flush()

    terms = FixedIncomeTerms(
        asset_id=asset.id,
        issuer=payload.issuer.strip(),
        rate_kind=payload.rate_kind,
        annual_rate_pct=payload.annual_rate_pct,
        issued_on=payload.issued_on,
        matures_on=payload.matures_on,
        has_secondary_market=payload.has_secondary_market,
        withholding_pct=payload.withholding_pct,
        notes=payload.notes,
    )
    db.add(terms)
    db.commit()

    fixed_income_service.publish_accruals(db)
    db.refresh(asset)
    return _read(db, asset, terms)


@router.get("/{symbol}", response_model=FixedIncomeRead)
def get_instrument(db: DbSession, symbol: str) -> FixedIncomeRead:
    asset = db.scalar(select(Asset).where(Asset.symbol == symbol.strip().upper()))
    if asset is None or asset.asset_type is not AssetType.FIXED_INCOME:
        raise NotFoundError(f"No hay instrumento de renta fija con símbolo {symbol}.")
    terms = fixed_income_service.get_terms(db, asset)
    if terms is None:
        raise NotFoundError(f"{symbol} no tiene condiciones registradas.")
    return _read(db, asset, terms)


@router.delete("/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
def delete_instrument(
    db: DbSession,
    symbol: str,
    confirm: Annotated[bool, Query(description="Obligatorio: borra el activo")] = False,
) -> None:
    """Borra el instrumento. Falla si tiene transacciones: el ledger manda."""
    asset = db.scalar(select(Asset).where(Asset.symbol == symbol.strip().upper()))
    if asset is None or asset.asset_type is not AssetType.FIXED_INCOME:
        raise NotFoundError(f"No hay instrumento de renta fija con símbolo {symbol}.")
    if not confirm:
        raise DuplicateError(
            "Añade ?confirm=true para borrarlo. Si tiene operaciones registradas, "
            "bórralas primero: el ledger es la fuente de verdad."
        )
    if asset.transactions:
        raise DuplicateError(
            f"{symbol} tiene {len(asset.transactions)} operación(es) registradas. "
            f"Bórralas primero o el histórico dejaría de cuadrar."
        )
    db.delete(asset)
    db.commit()
