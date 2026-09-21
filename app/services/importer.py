"""Importa operaciones desde un CSV con una plantilla FIJA.

NO ADIVINA FORMATOS DE BROKER
=============================
Cada broker exporta columnas, fechas y decimales distintos, y adivinar mal es
peor que fallar: una cantidad leída con coma decimal equivocada corrompe el
coste medio para siempre. Aquí se acepta UNA plantilla (`TEMPLATE`), con
cabecera exacta, punto decimal y sin separador de miles. Si tu extracto es
otro, se convierte a esta plantilla, y cualquier desviación se rechaza con el
número de línea.

TODO O NADA
===========
Se valida el archivo entero contra el ledger actual reproduciéndolo en memoria
(mismas reglas estrictas que una alta manual: no se puede vender lo que no se
tiene, y hace falta tipo de cambio). Con un solo error no se escribe nada.

IDEMPOTENTE
===========
Cada fila lleva un `external_id`: el que pongas tú, o uno determinista derivado
de su contenido. Volver a importar el mismo archivo no duplica nada: las filas
ya presentes se saltan y se cuentan aparte.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.exceptions import InvalidLedgerOperation
from app.models import Asset, Portfolio, Transaction, TransactionType
from app.repositories import market as market_repo
from app.repositories import portfolio as portfolio_repo
from app.schemas.importing import (
    MAX_CSV_ROWS,
    ImportIssue,
    ImportPreviewRow,
    ImportReport,
)
from app.schemas.transaction import TransactionCreate
from app.services.pnl import replay_ledger
from app.services.transactions import resolve_fx

COLUMNS = (
    "date", "type", "symbol", "quantity", "price", "amount", "fees",
    "currency", "fx_rate_to_base", "notes", "external_id",
)
REQUIRED = ("date", "type")

def template_for(base_currency: str, usd_rate: Decimal | None = None) -> str:
    """La plantilla de ejemplo, en la divisa base de TU cartera.

    Era un texto fijo que daba por hecho una cartera en pesos: depositaba
    5.000.000 COP y ponía tipos de cambio de 4.150 en cada compra. Con la
    divisa base en dólares eso enseña exactamente lo contrario de lo que hay
    que hacer, y quien copia la plantilla tal cual monta un ledger equivocado.

    Las compras van en USD porque AAPL y KO cotizan en USD: eso es un hecho del
    activo, no de tu cartera, y el importador rechaza una divisa que no case
    con la del activo ya verificado.

    `fx_rate_to_base` va VACÍO cuando la divisa coincide con la base, porque se
    resuelve solo y es la forma de enseñar que no hay que buscar el tipo de
    cada día a mano — justo la columna que hace abandonar el importador.

    Cuando NO coincide se rellena con el tipo real (`usd_rate`), no se deja en
    blanco. La plantilla tiene que poder importarse TAL CUAL: es lo primero que
    alguien prueba, y una plantilla que su propio validador rechaza destruye la
    confianza en el importador entero. Si no hay tipo guardado queda vacía y el
    aviso de la fila lo dice, que es preferible a inventarse un número: poner
    un 1 en USD->COP erraría por un factor de ~3.000.
    """
    base = base_currency.upper()
    if base == "USD":
        fx, hint = "", "Deja fx_rate_to_base vacío y se resuelve solo"
    elif usd_rate is not None:
        fx, hint = f"{usd_rate}", "Tipo de cambio del día de la operación"
    else:
        fx, hint = "", f"Pon aquí el tipo USD->{base} de ese día"
    return (
        ",".join(COLUMNS) + "\n"
        f"2026-01-15,DEPOSIT,,,,5000,0,{base},1,"
        "Depósito inicial (borra estas filas de ejemplo),\n"
        f"2026-01-20,BUY,AAPL,2,185.50,,1.00,USD,{fx},{hint},\n"
        f"2026-02-10,SELL,AAPL,1,200.10,,1.00,USD,{fx},,\n"
        f"2026-03-15,DIVIDEND,KO,,,3.20,0.50,USD,{fx},,\n"
    )

# Una fecha sin hora es el mediodía de Bogotá: lejos de cualquier cambio de día
# en UTC, así que la fecha que escribes es la fecha que se guarda.
_NOON_BOGOTA = dt.timezone(dt.timedelta(hours=-5))

_PREVIEW_LIMIT = 200


class _RowError(Exception):
    pass


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _decimal(row: dict[str, str | None], column: str) -> Decimal | None:
    raw = _clean(row.get(column))
    if raw is None:
        return None
    if "," in raw:
        raise _RowError(
            f"«{column}» = «{raw}»: usa punto decimal y sin separador de miles (1234.56)"
        )
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise _RowError(f"«{column}» = «{raw}» no es un número") from None


def _parse_date(raw: str | None) -> dt.datetime:
    raw = _clean(raw)
    if raw is None:
        raise _RowError("Falta la fecha")
    try:
        if len(raw) == 10:
            day = dt.date.fromisoformat(raw)
            return dt.datetime(day.year, day.month, day.day, 12, 0, tzinfo=_NOON_BOGOTA)
        moment = dt.datetime.fromisoformat(raw)
    except ValueError:
        raise _RowError(
            f"Fecha «{raw}» no válida: usa AAAA-MM-DD o AAAA-MM-DDTHH:MM:SS-05:00"
        ) from None
    if moment.tzinfo is None:
        raise _RowError(
            f"Fecha «{raw}» sin zona horaria: añade el desfase (ej. -05:00) o usa solo AAAA-MM-DD"
        )
    return moment


def _external_id(row: dict[str, str | None], occurrence: int) -> str:
    """Id determinista del CONTENIDO de la fila, con su nº de aparición: dos
    compras idénticas legítimas en el mismo archivo no chocan entre sí."""
    basis = "|".join(
        (_clean(row.get(c)) or "") for c in COLUMNS if c not in ("notes", "external_id")
    )
    digest = hashlib.sha1(f"{basis}|{occurrence}".encode()).hexdigest()[:16]
    return f"csv-{digest}"


@dataclass
class _Candidate:
    line: int
    payload: TransactionCreate
    external_id: str
    currency: str
    fx: Decimal
    asset: Asset | None  # None = símbolo desconocido, se crea al aplicar
    asset_key: int | None  # id real, o negativo si es nuevo
    duplicate: bool = False


def _parse(
    db: Session, portfolio: Portfolio, text: str
) -> tuple[list[_Candidate], list[ImportIssue], list[str], int]:
    errors: list[ImportIssue] = []
    warnings: list[str] = []
    candidates: list[_Candidate] = []

    text = text.lstrip("﻿")
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]

    if len(header) == 1 and (";" in header[0] or "\t" in header[0]):
        errors.append(ImportIssue(
            line=1,
            message="El separador debe ser la coma (,). Tu archivo parece usar «;» o tabuladores.",
        ))
        return [], errors, warnings, 0

    missing = [c for c in REQUIRED if c not in header]
    unknown = [h for h in header if h not in COLUMNS]
    if missing or unknown:
        if missing:
            errors.append(ImportIssue(line=1, message=f"Faltan columnas: {', '.join(missing)}"))
        if unknown:
            errors.append(ImportIssue(
                line=1,
                message=(
                    f"Columnas desconocidas: {', '.join(unknown)}. No se adivinan formatos de "
                    f"broker: usa exactamente la plantilla ({', '.join(COLUMNS)})."
                ),
            ))
        return [], errors, warnings, 0
    reader.fieldnames = header

    existing_ids = {
        t.external_id for t in portfolio_repo.get_transactions(db, portfolio.id) if t.external_id
    }
    # La línea se lee MIENTRAS se itera: una vez agotado el lector `line_num`
    # vale siempre la última y todos los errores apuntarían a la misma fila.
    numbered = [(reader.line_num, row) for row in reader]
    rows = [row for _, row in numbered]
    if len(rows) > MAX_CSV_ROWS:
        errors.append(ImportIssue(
            line=None, message=f"Demasiadas filas ({len(rows)}). El máximo es {MAX_CSV_ROWS}."
        ))
        return [], errors, warnings, 0

    wanted = {(_clean(r.get("symbol")) or "").upper() for r in rows} - {""}
    known = market_repo.get_assets_by_symbols(db, sorted(wanted))

    seen_content: Counter[str] = Counter()
    seen_ids: set[str] = set()
    next_temp_id = -1
    temp_ids: dict[str, int] = {}

    total = 0
    for line, row in numbered:
        if not any(_clean(v) for v in row.values() if isinstance(v, str)):
            continue
        total += 1
        if None in row:
            errors.append(ImportIssue(line=line, message="Tiene más columnas que la cabecera"))
            continue

        try:
            executed_at = _parse_date(row.get("date"))
            type_raw = (_clean(row.get("type")) or "").upper()
            try:
                tx_type = TransactionType(type_raw)
            except ValueError:
                raise _RowError(
                    f"Tipo «{type_raw}» no válido: usa BUY, SELL, DIVIDEND, DEPOSIT o WITHDRAWAL"
                ) from None

            symbol = (_clean(row.get("symbol")) or "").upper() or None
            currency = (_clean(row.get("currency")) or "").upper() or None
            asset = known.get(symbol) if symbol else None

            if symbol and currency is None and (asset is None or asset.last_verified_at is None):
                raise _RowError(
                    f"«{symbol}» no está verificado en el catálogo: indica la columna "
                    "currency (la divisa en que cotiza) para no adivinarla"
                )
            if asset is not None and asset.last_verified_at is not None and currency and (
                currency != asset.currency.upper()
            ):
                raise _RowError(
                    f"La divisa {currency} no coincide con la de {symbol} en el catálogo "
                    f"({asset.currency})"
                )

            payload = TransactionCreate(
                type=tx_type,
                executed_at=executed_at,
                symbol=symbol,
                quantity=_decimal(row, "quantity"),
                price=_decimal(row, "price"),
                cash_amount=_decimal(row, "amount"),
                fees=_decimal(row, "fees") or Decimal("0"),
                currency=currency,
                fx_rate_to_base=_decimal(row, "fx_rate_to_base"),
                notes=_clean(row.get("notes")),
            )

            effective_currency = (
                currency or (asset.currency if asset else None) or portfolio.base_currency
            ).upper()
            fx = resolve_fx(db, payload, effective_currency, portfolio)

            content_key = "|".join((_clean(row.get(c)) or "") for c in COLUMNS[:9])
            seen_content[content_key] += 1
            explicit = _clean(row.get("external_id"))
            external_id = explicit or _external_id(row, seen_content[content_key])
            if external_id in seen_ids:
                raise _RowError(f"external_id «{external_id}» repetido dentro del archivo")
            seen_ids.add(external_id)

            if symbol and asset is None and symbol not in temp_ids:
                temp_ids[symbol] = next_temp_id
                next_temp_id -= 1

            candidates.append(_Candidate(
                line=line,
                payload=payload,
                external_id=external_id,
                currency=effective_currency,
                fx=fx,
                asset=asset,
                asset_key=(asset.id if asset else temp_ids.get(symbol) if symbol else None),
                duplicate=external_id in existing_ids,
            ))
        except _RowError as exc:
            errors.append(ImportIssue(line=line, message=str(exc)))
        except ValidationError as exc:
            for err in exc.errors():
                where = ".".join(str(p) for p in err["loc"])
                msg = str(err["msg"]).removeprefix("Value error, ")
                errors.append(ImportIssue(line=line, message=f"{where}: {msg}" if where else msg))
        except InvalidLedgerOperation as exc:
            errors.append(ImportIssue(line=line, message=str(exc)))

    if not rows:
        errors.append(ImportIssue(line=None, message="El archivo no tiene filas de datos."))
    return candidates, errors, warnings, total


def _as_transaction(portfolio: Portfolio, c: _Candidate, asset_id: int | None) -> Transaction:
    p = c.payload
    return Transaction(
        portfolio_id=portfolio.id,
        asset_id=asset_id,
        type=p.type,
        executed_at=p.executed_at,
        quantity=p.quantity,
        price=p.price,
        cash_amount=p.cash_amount,
        fees=p.fees,
        currency=c.currency,
        fx_rate_to_base=c.fx,
        notes=p.notes,
        external_id=c.external_id,
    )


def _validate_ledger(
    db: Session, portfolio: Portfolio, fresh: list[_Candidate]
) -> tuple[list[ImportIssue], list[str]]:
    """Reproduce el ledger actual + las filas nuevas con las reglas estrictas.

    Si falla, se localiza la PRIMERA fila (por fecha) que rompe el ledger con
    una búsqueda binaria sobre el prefijo: añadir filas posteriores no puede
    arreglar una venta descubierta, así que el fallo es monótono.
    """
    existing = portfolio_repo.get_transactions(db, portfolio.id)
    ordered = sorted(fresh, key=lambda c: (c.payload.executed_at, c.line))
    built = [_as_transaction(portfolio, c, c.asset_key) for c in ordered]

    def fails(k: int) -> str | None:
        try:
            replay_ledger([*existing, *built[:k]], strict=True)
        except InvalidLedgerOperation as exc:
            return str(exc)
        return None

    if fails(len(built)) is None:
        cash = replay_ledger([*existing, *built], strict=False).cash_balance
        warnings = []
        if cash < 0:
            warnings.append(
                "Con estas operaciones el efectivo de la cartera queda negativo "
                f"({cash:,.0f} {portfolio.base_currency}): faltan depósitos por registrar. "
                "No impide importar, pero el efectivo y el rendimiento no cuadrarán "
                "hasta añadirlos."
            )
        return [], warnings

    low, high = 1, len(built)
    while low < high:
        mid = (low + high) // 2
        if fails(mid) is not None:
            high = mid
        else:
            low = mid + 1
    culprit = ordered[low - 1]
    message = fails(low) or "El ledger resultante no es válido"
    if culprit.payload.symbol:
        # El ledger habla de ids internos ("activo 24"), que no le dicen nada a
        # quien mira su CSV: se nombra el símbolo de la fila señalada.
        message = re.sub(r"del activo -?\d+", f"de {culprit.payload.symbol}", message)
    hint = (
        " Si vendes y compras el mismo día, escribe la hora (AAAA-MM-DDTHH:MM:SS-05:00) "
        "para fijar el orden."
    )
    return [ImportIssue(line=culprit.line, message=message + "." + hint)], []


def _preview(c: _Candidate) -> ImportPreviewRow:
    p = c.payload
    return ImportPreviewRow(
        line=c.line,
        status="duplicate" if c.duplicate else "new",
        date=p.executed_at.astimezone(_NOON_BOGOTA).date().isoformat(),
        type=p.type.value,
        symbol=p.symbol,
        quantity=None if p.quantity is None else str(p.quantity),
        price=None if p.price is None else str(p.price),
        amount=None if p.cash_amount is None else str(p.cash_amount),
        fees=str(p.fees),
        currency=c.currency,
        fx_rate_to_base=str(c.fx),
    )


def import_csv(
    db: Session, portfolio: Portfolio, text: str, *, dry_run: bool = True
) -> ImportReport:
    candidates, errors, warnings, total = _parse(db, portfolio, text)
    fresh = [c for c in candidates if not c.duplicate]
    duplicates = len(candidates) - len(fresh)

    if not errors and fresh:
        ledger_errors, ledger_warnings = _validate_ledger(db, portfolio, fresh)
        errors.extend(ledger_errors)
        warnings.extend(ledger_warnings)

    new_symbols = sorted({c.payload.symbol for c in fresh if c.payload.symbol and c.asset is None})
    if new_symbols:
        warnings.append(
            f"Símbolos que no están en tu catálogo y se darán de alta: {', '.join(new_symbols)}. "
            "Sus datos de mercado se traerán en la próxima sincronización."
        )
    if duplicates:
        warnings.append(
            f"{duplicates} fila(s) ya estaban importadas (mismo external_id) y se saltan."
        )

    report = ImportReport(
        dry_run=dry_run,
        applied=False,
        total_rows=total,
        new_rows=len(fresh),
        duplicate_rows=duplicates,
        errors=errors,
        warnings=warnings,
        new_symbols=new_symbols,
        by_type=dict(Counter(c.payload.type.value for c in fresh)),
        preview=[_preview(c) for c in candidates[:_PREVIEW_LIMIT]],
        message="",
    )

    if errors:
        report.message = (
            f"No se importó nada: hay {len(errors)} problema(s) por corregir. "
            "Se importa todo o nada."
        )
        return report
    if not fresh:
        report.message = (
            "No hay operaciones nuevas: todas las filas ya estaban importadas."
            if duplicates else "El archivo no tiene operaciones."
        )
        return report
    if dry_run:
        report.message = (
            f"Revisión correcta: {len(fresh)} operación(es) listas para importar. "
            "Todavía no se ha escrito nada."
        )
        return report

    # --- Aplicar: una sola transacción de base de datos ---
    asset_ids: dict[str, int] = {}
    for c in sorted(fresh, key=lambda c: (c.payload.executed_at, c.line)):
        symbol = c.payload.symbol
        asset_id = None
        if symbol:
            if symbol not in asset_ids:
                asset = c.asset or portfolio_repo.get_or_create_asset(
                    db, symbol, currency=c.currency
                )
                asset_ids[symbol] = asset.id
            asset_id = asset_ids[symbol]
        db.add(_as_transaction(portfolio, c, asset_id))
    db.commit()

    report.applied = True
    report.message = f"Importadas {len(fresh)} operación(es)."
    return report
