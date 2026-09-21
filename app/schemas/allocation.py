"""Esquemas del plan de asignación entre clases de activo."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class AllocationTargetWrite(BaseModel):
    """Un objetivo. La banda tiene valor por defecto porque casi nadie la piensa."""

    model_config = ConfigDict(extra="forbid")

    asset_class: str = Field(
        description="accion | fondo_acciones | renta_fija | materias_primas | cripto"
    )
    target_pct: Decimal = Field(ge=0, le=100)
    band_pct: Decimal = Field(
        default=Decimal("5"),
        ge=0,
        le=100,
        description=(
            "Tolerancia en PUNTOS. Sin ella, cualquier movimiento del mercado "
            "obligaría a rebalancear y eso cuesta comisiones e impuestos"
        ),
    )
    notes: str | None = Field(default=None, max_length=300)


class AllocationPlanWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[AllocationTargetWrite]


class ClassPositionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_class: str
    label: str
    current_pct: Decimal
    current_amount: Decimal
    target_pct: Decimal | None = None
    band_pct: Decimal | None = None
    drift_pct: Decimal | None = Field(
        default=None, description="Actual menos objetivo, en puntos porcentuales"
    )
    status: str = Field(description="dentro | por_debajo | por_encima | sin_plan")
    gap_amount: Decimal | None = Field(
        default=None, description="Cuánto falta (o sobra, en negativo) para el objetivo"
    )
    notes: str | None = None


class AllocationReportRead(BaseModel):
    """El plan frente a la realidad. NO propone vender: ver `services/allocation.py`."""

    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    base_currency: str
    has_plan: bool
    planned_pct: Decimal = Field(description="Cuánto suman los objetivos declarados")
    total_value: Decimal | None
    positions: list[ClassPositionRead]
    contribution_order: list[str] = Field(
        default_factory=list,
        description=(
            "Clases a las que dirigir el dinero NUEVO, de la más rezagada a la "
            "menos. Rebalancear con aportes evita realizar ganancias"
        ),
    )
    warnings: list[str] = Field(default_factory=list)
    disclaimer: str = Field(
        default=(
            "El plan es TUYO: el tablero solo mide la desviación. No es "
            "asesoramiento financiero y no propone vender."
        )
    )
