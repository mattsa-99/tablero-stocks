"""Esquemas de la importación de operaciones desde CSV."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

MAX_CSV_BYTES = 2_000_000
MAX_CSV_ROWS = 5000


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    csv: str = Field(min_length=1, max_length=MAX_CSV_BYTES, description="Contenido del CSV")


class ImportIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: int | None = Field(
        default=None, description="Línea del archivo (la cabecera es la 1). None = todo el archivo"
    )
    message: str


class ImportPreviewRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line: int
    status: str = Field(description="new | duplicate")
    date: str
    type: str
    symbol: str | None = None
    quantity: str | None = None
    price: str | None = None
    amount: str | None = None
    fees: str
    currency: str
    fx_rate_to_base: str


class ImportReport(BaseModel):
    """Resultado de revisar (o aplicar) un CSV.

    TODO O NADA: si hay un solo error no se importa ninguna fila. Importar la
    mitad de un archivo deja una cartera que parece completa y no lo es, que es
    peor que no tener cartera.
    """

    model_config = ConfigDict(extra="forbid")

    dry_run: bool
    applied: bool
    total_rows: int
    new_rows: int
    duplicate_rows: int
    errors: list[ImportIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    new_symbols: list[str] = Field(default_factory=list)
    by_type: dict[str, int] = Field(default_factory=dict)
    preview: list[ImportPreviewRow] = Field(default_factory=list)
    message: str
