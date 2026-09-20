"""Diario de decisiones y lista de vigilancia.

Guarda TUS razones (tesis, qué te haría cambiar de opinión, cuándo revisar) y
una foto de lo que decía el Tablero ese día. Después las compara con lo que
dice hoy y avisa cuando algo pide tu atención.

Una alerta pide REVISAR, nunca vender: que el precio cruzara tu nivel de
invalidación no significa que la tesis haya muerto, significa que tu propia
regla dice que vuelvas a mirarla con la cabeza fría.

No toca el ledger. Anotar «BUY» no compra nada.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import InsufficientUniverse, InvalidJournalEntry, NotFoundError
from app.models import Asset, JournalEntry, Portfolio
from app.models.enums import JournalKind
from app.repositories import market as market_repo
from app.schemas.journal import (
    DEFAULT_REVIEW_DAYS,
    JournalAlert,
    JournalCounts,
    JournalCreate,
    JournalEntryRead,
    JournalListResponse,
    JournalNow,
    JournalReview,
    JournalSnapshot,
    JournalUpdate,
    SnapshotFlag,
)
from app.schemas.opportunity import OpportunityRead
from app.services import ficha as ficha_service
from app.services import opportunities as opportunity_service
from app.services import portfolio as portfolio_service
from app.services import universe as universe_service

REVIEW_SOON_DAYS = 7
# Cuántos escalones debe bajar la calificación para avisar. Un solo escalón
# (Buena -> Normal) es ruido; dos ya es un cambio de fondo.
GRADE_DROP_TO_ALERT = 2
GRADE_LEVEL = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}

_ALERT_ORDER = {"red": 0, "yellow": 1, "info": 2}


# ---------------------------------------------------------------------------
# Escritura
# ---------------------------------------------------------------------------


def _future(date: dt.date, today: dt.date, what: str) -> None:
    if date <= today:
        raise InvalidJournalEntry(
            f"{what} debe ser una fecha futura (recibida: {date.isoformat()})"
        )


def get_asset(db: Session, symbol: str) -> Asset:
    symbol = symbol.strip().upper()
    asset = market_repo.get_assets_by_symbols(db, [symbol]).get(symbol)
    if asset is None:
        raise NotFoundError(
            f"El símbolo {symbol} no está en el catálogo. Búscalo y añádelo primero."
        )
    return asset


def get_entry(db: Session, entry_id: int) -> JournalEntry:
    entry = db.get(JournalEntry, entry_id)
    if entry is None:
        raise NotFoundError(f"Entrada {entry_id} del diario no encontrada")
    return entry


def _take_snapshot(db: Session, portfolio: Portfolio, asset: Asset) -> dict:
    """La foto del Tablero de hoy. Si no se puede puntuar, solo el precio."""
    quote = market_repo.get_quotes(db, [asset.id]).get(asset.id)
    snapshot: dict = {"snapshot_price": quote.price if quote else None}
    try:
        ficha = ficha_service.build_ficha(db, portfolio, asset.symbol)
    except InsufficientUniverse:
        return snapshot

    snapshot["snapshot_verdict"] = ficha.verdict.level
    snapshot["snapshot_flags"] = [{"code": f.code, "level": f.level} for f in ficha.flags]
    if ficha.opportunity is not None:
        row = ficha.opportunity
        snapshot.update(
            snapshot_price=row.current_price if row.current_price is not None
            else snapshot["snapshot_price"],
            snapshot_score=row.score,
            snapshot_rank=row.rank,
            snapshot_grade=row.assessment.grade,
        )
    return snapshot


def create_entry(
    db: Session,
    portfolio: Portfolio,
    payload: JournalCreate,
    *,
    today: dt.date | None = None,
) -> JournalEntry:
    today = today or dt.date.today()
    asset = get_asset(db, payload.symbol)

    review_date = payload.review_date or today + dt.timedelta(days=DEFAULT_REVIEW_DAYS)
    _future(review_date, today, "La fecha de revisión")

    entry = JournalEntry(
        portfolio_id=portfolio.id,
        asset_id=asset.id,
        kind=payload.kind,
        is_active=True,
        thesis=payload.thesis,
        invalidation=payload.invalidation,
        invalidation_price=payload.invalidation_price,
        review_date=review_date,
        **_take_snapshot(db, portfolio, asset),
    )
    db.add(entry)
    db.commit()
    return entry


def update_entry(
    db: Session,
    entry_id: int,
    payload: JournalUpdate,
    *,
    today: dt.date | None = None,
) -> JournalEntry:
    today = today or dt.date.today()
    entry = get_entry(db, entry_id)
    sent = payload.model_fields_set

    if "kind" in sent and payload.kind is not None:
        entry.kind = payload.kind
    if "thesis" in sent and payload.thesis is not None:
        entry.thesis = payload.thesis
    if "invalidation" in sent:
        entry.invalidation = (payload.invalidation or "").strip() or None
    if "invalidation_price" in sent:
        entry.invalidation_price = payload.invalidation_price
    if "review_date" in sent and payload.review_date is not None:
        _future(payload.review_date, today, "La fecha de revisión")
        entry.review_date = payload.review_date
    if "is_active" in sent and payload.is_active is not None:
        entry.is_active = payload.is_active

    db.commit()
    return entry


def review_entry(
    db: Session,
    entry_id: int,
    payload: JournalReview,
    *,
    today: dt.date | None = None,
) -> JournalEntry:
    today = today or dt.date.today()
    entry = get_entry(db, entry_id)

    next_review = payload.next_review_date or today + dt.timedelta(days=DEFAULT_REVIEW_DAYS)
    if not payload.archive:
        _future(next_review, today, "La próxima revisión")
        entry.review_date = next_review

    entry.reviewed_at = dt.datetime.now(dt.UTC)
    entry.review_note = payload.note
    if payload.archive:
        entry.is_active = False
    db.commit()
    return entry


def delete_entry(db: Session, entry_id: int) -> None:
    entry = get_entry(db, entry_id)
    db.delete(entry)
    db.commit()


# ---------------------------------------------------------------------------
# Lectura y alertas
# ---------------------------------------------------------------------------


@dataclass
class _Context:
    today: dt.date
    rows: dict[str, OpportunityRead] = field(default_factory=dict)
    ranked: bool = False  # ¿se pudo puntuar el universo?
    quotes: dict[int, object] = field(default_factory=dict)
    held_ids: set[int] = field(default_factory=set)


def _alerts(entry: JournalEntry, asset: Asset, ctx: _Context) -> list[JournalAlert]:
    if not entry.is_active:
        return []
    alerts: list[JournalAlert] = []
    quote = ctx.quotes.get(asset.id)
    price = getattr(quote, "price", None)
    stale = bool(getattr(quote, "is_stale", False))

    if entry.invalidation_price is not None and price is not None:
        level = float(entry.invalidation_price)
        if price <= level:
            alerts.append(JournalAlert(
                code="invalidation_breached", level="red",
                text=(
                    f"El precio ({price:,.2f}) está en o por debajo de tu nivel de "
                    f"invalidación ({level:,.2f}). Tu propia regla dice que revises la "
                    "tesis con la cabeza fría"
                    + (" (ojo: el precio guardado puede estar obsoleto)." if stale else ".")
                ),
            ))

    days = (entry.review_date - ctx.today).days
    if days <= 0:
        alerts.append(JournalAlert(
            code="review_overdue", level="yellow",
            text=(
                f"Tu fecha de revisión era el {entry.review_date.isoformat()}"
                + (f" (hace {-days} días)." if days < 0 else " (es hoy).")
            ),
        ))
    elif days <= REVIEW_SOON_DAYS:
        alerts.append(JournalAlert(
            code="review_soon", level="info",
            text=f"Toca revisar en {days} días ({entry.review_date.isoformat()}).",
        ))

    if ctx.ranked:
        row = ctx.rows.get(asset.symbol)
        if row is None and entry.snapshot_score is not None:
            alerts.append(JournalAlert(
                code="not_ranked_now", level="yellow",
                text="Ya no aparece en el ranking (faltan datos): no hay forma de compararla hoy.",
            ))
        elif row is not None:
            then = GRADE_LEVEL.get(entry.snapshot_grade or "")
            now = GRADE_LEVEL.get(row.assessment.grade)
            if then and now and then - now >= GRADE_DROP_TO_ALERT:
                alerts.append(JournalAlert(
                    code="grade_dropped", level="yellow",
                    text=(
                        f"Su calificación pasó de «{_grade_label(entry.snapshot_grade)}» a "
                        f"«{row.assessment.label}» desde que la anotaste."
                    ),
                ))

    alerts.sort(key=lambda a: _ALERT_ORDER[a.level])
    return alerts


def _grade_label(grade: str | None) -> str:
    return {
        "A": "Muy buena", "B": "Buena", "C": "Normal", "D": "Mala", "E": "Muy mala",
    }.get(grade or "", grade or "sin calificar")


def _read(entry: JournalEntry, ctx: _Context) -> JournalEntryRead:
    asset = entry.asset
    quote = ctx.quotes.get(asset.id)
    price = getattr(quote, "price", None)
    row = ctx.rows.get(asset.symbol)

    change = None
    if price is not None and entry.snapshot_price:
        change = (price / entry.snapshot_price - 1.0) * 100

    return JournalEntryRead(
        id=entry.id,
        portfolio_id=entry.portfolio_id,
        symbol=asset.symbol,
        name=asset.name,
        currency=asset.currency,
        kind=entry.kind,
        is_active=entry.is_active,
        thesis=entry.thesis,
        invalidation=entry.invalidation,
        invalidation_price=entry.invalidation_price,
        review_date=entry.review_date,
        days_to_review=(entry.review_date - ctx.today).days,
        reviewed_at=entry.reviewed_at,
        review_note=entry.review_note,
        created_at=entry.created_at,
        held=asset.id in ctx.held_ids,
        snapshot=JournalSnapshot(
            price=entry.snapshot_price,
            score=entry.snapshot_score,
            rank=entry.snapshot_rank,
            grade=entry.snapshot_grade,
            verdict=entry.snapshot_verdict,
            flags=[SnapshotFlag(**f) for f in (entry.snapshot_flags or [])],
        ),
        now=JournalNow(
            price=price,
            price_stale=bool(getattr(quote, "is_stale", False)) if quote else None,
            change_since_entry_pct=change,
            score=row.score if row else None,
            rank=row.rank if row else None,
            grade=row.assessment.grade if row else None,
            grade_label=row.assessment.label if row else None,
        ),
        alerts=_alerts(entry, asset, ctx),
    )


def _entries(
    db: Session, portfolio: Portfolio, *, include_archived: bool, kind: JournalKind | None
) -> list[JournalEntry]:
    stmt = select(JournalEntry).where(JournalEntry.portfolio_id == portfolio.id)
    if not include_archived:
        stmt = stmt.where(JournalEntry.is_active.is_(True))
    if kind is not None:
        stmt = stmt.where(JournalEntry.kind == kind)
    return list(db.scalars(stmt.order_by(JournalEntry.review_date, JournalEntry.id)).all())


def _context(
    db: Session, portfolio: Portfolio, entries: Iterable[JournalEntry], *, score: bool,
    today: dt.date,
) -> _Context:
    entries = list(entries)
    ctx = _Context(today=today)
    asset_ids = [e.asset_id for e in entries]
    ctx.quotes = market_repo.get_quotes(db, asset_ids)
    ctx.held_ids = {a.id for a in portfolio_service.get_portfolio_assets(db, portfolio)}

    if score and entries:
        assets = universe_service.get_ingestion_universe(db)
        try:
            response = opportunity_service.compute_opportunities(
                db, portfolio, assets=assets, limit=len(assets)
            )
        except InsufficientUniverse:
            return ctx
        ctx.rows = {r.symbol: r for r in response.opportunities}
        ctx.ranked = True
    return ctx


def _counts(reads: list[JournalEntryRead]) -> JournalCounts:
    active = [r for r in reads if r.is_active]
    return JournalCounts(
        active=len(active),
        overdue=sum(1 for r in active if any(a.code == "review_overdue" for a in r.alerts)),
        breached=sum(
            1 for r in active if any(a.code == "invalidation_breached" for a in r.alerts)
        ),
        with_alerts=sum(1 for r in active if any(a.level in ("red", "yellow") for a in r.alerts)),
    )


def list_entries(
    db: Session,
    portfolio: Portfolio,
    *,
    include_archived: bool = False,
    kind: JournalKind | None = None,
    today: dt.date | None = None,
) -> JournalListResponse:
    today = today or dt.date.today()
    entries = _entries(db, portfolio, include_archived=include_archived, kind=kind)
    ctx = _context(db, portfolio, entries, score=True, today=today)
    reads = [_read(e, ctx) for e in entries]

    # Lo que pide atención, primero: rojas, luego amarillas, luego por fecha.
    def order(read: JournalEntryRead) -> tuple:
        worst = min((_ALERT_ORDER[a.level] for a in read.alerts), default=3)
        return (not read.is_active, worst, read.review_date, read.id)

    reads.sort(key=order)
    return JournalListResponse(
        as_of=dt.datetime.now(dt.UTC), entries=reads, counts=_counts(reads)
    )


def summary(
    db: Session, portfolio: Portfolio, *, today: dt.date | None = None
) -> JournalCounts:
    """Solo los contadores, SIN puntuar el universo: es lo que pide cada página
    para el globo de alertas, y tiene que ser barato. Por eso no incluye las
    alertas que dependen del ranking (calificación caída)."""
    today = today or dt.date.today()
    entries = _entries(db, portfolio, include_archived=False, kind=None)
    ctx = _context(db, portfolio, entries, score=False, today=today)
    return _counts([_read(e, ctx) for e in entries])


def read_entry(
    db: Session, entry: JournalEntry, portfolio: Portfolio, *, today: dt.date | None = None
) -> JournalEntryRead:
    today = today or dt.date.today()
    ctx = _context(db, portfolio, [entry], score=True, today=today)
    return _read(entry, ctx)


__all__ = [
    "create_entry",
    "delete_entry",
    "get_asset",
    "get_entry",
    "list_entries",
    "read_entry",
    "review_entry",
    "summary",
    "update_entry",
]

