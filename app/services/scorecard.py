"""Captura de la foto del ranking y su evaluación hacia adelante.

Ver el docstring de `app/models/scorecard.py` para por qué esto no se puede
derivar después. Aquí está el cómo.

LA REGLA QUE LO HACE HONESTO: SE COMPARA DENTRO DE LA CLASE
===========================================================
«¿Rindieron más los A que los D?» solo significa algo dentro de una clase. Si
se mezclaran, el resultado mediría sobre todo qué clase tuvo mejor año: un
2022 con las acciones cayendo y las materias primas subiendo diría que las
calificaciones funcionan al revés, cuando lo que habría medido es otra cosa.

Y SE COMPARA CONTRA UN ÍNDICE DE SU CLASE. Que los A subieran un 12% no dice
nada por sí solo: si su clase entera subió un 15%, la calificación restó.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.bulk import insert_ignore_duplicates
from app.models import Asset, Portfolio, RankingSnapshot
from app.repositories import market as market_repo
from app.schemas.opportunity import OpportunityRead, OpportunityResponse

# Cuántos días tiene que haber pasado desde la última foto para tomar otra.
# Semanal: el score se mueve a diario con el precio, pero la CALIFICACIÓN
# -que es lo que se quiere validar- cambia de escalón muy despacio, y una foto
# diaria multiplicaría las filas por siete sin añadir información.
CAPTURE_EVERY_DAYS = 7


@dataclass
class GradeOutcome:
    """Cómo le fue a una calificación dentro de una clase."""

    asset_class: str
    grade: str
    count: int
    median_return_pct: float
    median_drawdown_pct: float | None
    class_median_return_pct: float
    excess_vs_class_pct: float


@dataclass
class Scorecard:
    captured_on: dt.date
    evaluated_on: dt.date
    horizon_days: int
    outcomes: list[GradeOutcome]
    warnings: list[str] = field(default_factory=list)

    @property
    def is_conclusive(self) -> bool:
        """SIEMPRE False, y está puesto para que nadie tenga que preguntarlo.

        Con cinco calificaciones, cinco clases y un horizonte de meses, nada
        de lo que salga aquí alcanza significación estadística. Sirve para
        detectar lo CONTRARIO de lo esperado, no para confirmar nada.
        """
        return False


def last_capture(db: Session, portfolio: Portfolio) -> dt.date | None:
    return db.scalar(
        select(RankingSnapshot.captured_on)
        .where(RankingSnapshot.portfolio_id == portfolio.id)
        .order_by(RankingSnapshot.captured_on.desc())
        .limit(1)
    )


def is_capture_due(db: Session, portfolio: Portfolio, *, today: dt.date | None = None) -> bool:
    today = today or dt.date.today()
    ultima = last_capture(db, portfolio)
    return ultima is None or (today - ultima).days >= CAPTURE_EVERY_DAYS


def capture(
    db: Session,
    portfolio: Portfolio,
    response: OpportunityResponse | None = None,
    *,
    rows: Sequence[OpportunityRead] | None = None,
    today: dt.date | None = None,
) -> int:
    """Guarda el ranking COMPLETO, no las filas visibles.

    `response.opportunities` viene recortado por `limit` y filtrado por la
    vista. Guardar eso haría que la foto dependiera de qué estaba mirando el
    usuario al capturarla, que es justo lo que la invalidaría.
    """
    today = today or dt.date.today()
    filas = rows if rows is not None else (response.opportunities if response else [])
    if not filas:
        return 0

    payload = [
        {
            "captured_on": today,
            "portfolio_id": portfolio.id,
            "symbol": fila.symbol,
            "asset_class": fila.asset_class,
            "score": fila.score,
            "grade": fila.assessment.grade,
            "rank": fila.rank,
            "class_size": fila.class_size,
            "available_signals": fila.assessment.available_signals,
            "price": fila.current_price,
            "currency": fila.currency,
            "created_at": dt.datetime.now(dt.UTC),
        }
        for fila in filas
    ]
    return insert_ignore_duplicates(
        db,
        RankingSnapshot.__table__,
        payload,
        index_elements=["captured_on", "portfolio_id", "symbol"],
    )


def evaluate(
    db: Session,
    portfolio: Portfolio,
    *,
    captured_on: dt.date,
    until: dt.date | None = None,
) -> Scorecard:
    """Compara el rendimiento por calificación desde una foto concreta."""
    until = until or dt.date.today()
    avisos: list[str] = []

    fotos = list(db.scalars(
        select(RankingSnapshot).where(
            RankingSnapshot.portfolio_id == portfolio.id,
            RankingSnapshot.captured_on == captured_on,
        )
    ).all())
    if not fotos:
        return Scorecard(captured_on, until, 0, [], [f"No hay foto del {captured_on}."])

    horizonte = (until - captured_on).days
    if horizonte < 180:
        avisos.append(
            f"Solo han pasado {horizonte} días desde la foto. A menos de seis "
            f"meses cualquier diferencia es ruido de mercado, no señal."
        )

    simbolos = {f.symbol for f in fotos}
    activos = {
        a.symbol: a
        for a in db.scalars(select(Asset).where(Asset.symbol.in_(simbolos))).all()
    }
    series = market_repo.get_price_series_dated(
        db, [a.id for a in activos.values()], since=captured_on
    )

    # Rendimiento de cada símbolo desde la foto hasta hoy, en divisa local.
    retornos: dict[str, float] = {}
    caidas: dict[str, float] = {}
    for foto in fotos:
        asset = activos.get(foto.symbol)
        if asset is None or foto.price is None or foto.price <= 0:
            continue
        serie = [p for d, p in series.get(asset.id, []) if captured_on <= d <= until]
        if len(serie) < 2:
            continue
        retornos[foto.symbol] = (serie[-1] / foto.price - 1.0) * 100.0
        pico, peor = serie[0], 0.0
        for precio in serie:
            pico = max(pico, precio)
            if pico > 0:
                peor = max(peor, 1.0 - precio / pico)
        caidas[foto.symbol] = peor * 100.0

    if not retornos:
        avisos.append("No hay precios posteriores a la foto para medir nada.")
        return Scorecard(captured_on, until, horizonte, [], avisos)

    por_clase: dict[str, list[RankingSnapshot]] = {}
    for foto in fotos:
        if foto.symbol in retornos:
            por_clase.setdefault(foto.asset_class, []).append(foto)

    resultados: list[GradeOutcome] = []
    for clase, miembros in sorted(por_clase.items()):
        mediana_clase = statistics.median(retornos[f.symbol] for f in miembros)
        por_nota: dict[str, list[RankingSnapshot]] = {}
        for foto in miembros:
            por_nota.setdefault(foto.grade, []).append(foto)
        for nota, grupo in sorted(por_nota.items()):
            rets = [retornos[f.symbol] for f in grupo]
            dds = [caidas[f.symbol] for f in grupo if f.symbol in caidas]
            mediana = statistics.median(rets)
            resultados.append(GradeOutcome(
                asset_class=clase,
                grade=nota,
                count=len(grupo),
                median_return_pct=round(mediana, 2),
                median_drawdown_pct=round(statistics.median(dds), 2) if dds else None,
                class_median_return_pct=round(mediana_clase, 2),
                excess_vs_class_pct=round(mediana - mediana_clase, 2),
            ))

    return Scorecard(captured_on, until, horizonte, resultados, avisos)
