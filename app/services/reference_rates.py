"""Refresco y lectura de las tasas de referencia colombianas.

QUÉ RESPONDE. «¿Este 12,3% es bueno?» no se puede contestar sin saber contra
qué. Aquí están las tres referencias que lo deciden:

    inflación anual   -> si la tasa no la supera, pierdes poder adquisitivo
    política del Banrep -> el suelo de lo que paga el dinero sin riesgo a plazo
    TES y CDT de mercado -> lo que pagan realmente instrumentos comparables

LA FECHA VIAJA SIEMPRE con el valor. La curva TES se publica con unos días de
retraso y la inflación es mensual: presentar «12,66%» sin decir de cuándo es
invita a leerlo como el dato de hoy.
"""

from __future__ import annotations

import datetime as dt
import logging
import statistics
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import ProviderError
from app.db.bulk import upsert
from app.models import CdtOffer, ReferenceRate
from app.providers.banrep import BY_KEY, SERIES, BanrepClient
from app.providers.datos_gov import PLAZOS, DatosGovClient

logger = logging.getLogger(__name__)

# Cuánto histórico se guarda. Cinco años cubre un ciclo completo de tasas en
# Colombia (del 1,75% de 2021 al 13,25% de 2023) y es lo que hace legible un
# «12% es alto o bajo».
HISTORY_YEARS = 5


@dataclass(frozen=True)
class RateReading:
    """Un valor con su fecha y su etiqueta. La fecha nunca es opcional."""

    series: str
    label: str
    value: float
    as_of: dt.date
    unit: str
    # Qué representa. Decide si tiene sentido descontarle la inflación; ver
    # `providers/banrep.py::RateSeries`.
    nature: str = "nominal"

    @property
    def is_nominal_rate(self) -> bool:
        return self.nature == "nominal"

    @property
    def is_already_real(self) -> bool:
        return self.nature == "real"

    @property
    def is_stale_monthly(self) -> bool:
        """True si tiene más de 45 días: una serie mensual normal no los pasa."""
        return (dt.date.today() - self.as_of).days > 45


def refresh(
    db: Session, client: BanrepClient | None = None, *, keys: list[str] | None = None
) -> tuple[int, list[str]]:
    """Trae las series y las guarda. Devuelve (filas escritas, avisos).

    Se usa `upsert` y no `insert_ignore_duplicates` por la misma razón que en
    el histórico de tipos de cambio: Banrep revisa series hacia atrás -lo dice
    en sus propias notas, la curva cero cupón se recalculó en 2021 para tres
    años de historia- y una fila vieja que no se pisa dejaría el dato corregido
    fuera para siempre.
    """
    client = client or BanrepClient()
    pedidas = keys or [s.key for s in SERIES]
    desde = dt.date.today() - dt.timedelta(days=365 * HISTORY_YEARS)
    avisos: list[str] = []

    try:
        series = client.fetch_series(pedidas)
    except ProviderError as exc:
        logger.warning("No se pudieron traer las tasas de referencia: %s", exc)
        return 0, [f"Tasas de referencia de Banrep no disponibles: {exc}"]

    ausentes = [k for k in pedidas if k not in series]
    if ausentes:
        avisos.append(f"Banrep no devolvió: {', '.join(sorted(ausentes))}")

    ahora = dt.datetime.now(dt.UTC)
    payload = [
        {
            "series": key,
            "as_of": fecha,
            "value": valor,
            "unit": BY_KEY[key].unit,
            "source": "banrep",
            "fetched_at": ahora,
        }
        for key, puntos in series.items()
        for fecha, valor in puntos
        if fecha >= desde
    ]
    if not payload:
        return 0, avisos

    escritas = upsert(
        db,
        ReferenceRate.__table__,
        payload,
        index_elements=["series", "as_of"],
        update_columns=["value", "unit", "source", "fetched_at"],
    )
    db.commit()
    return escritas, avisos


def latest(db: Session, keys: list[str] | None = None) -> dict[str, RateReading]:
    """Último valor de cada serie, con su fecha.

    Una sola consulta con el máximo por serie, no una por serie: la ficha de
    un instrumento pide media docena a la vez.
    """
    pedidas = keys or [s.key for s in SERIES]
    if not pedidas:
        return {}

    maximos = (
        select(ReferenceRate.series, func.max(ReferenceRate.as_of).label("as_of"))
        .where(ReferenceRate.series.in_(pedidas))
        .group_by(ReferenceRate.series)
        .subquery()
    )
    filas = db.execute(
        select(ReferenceRate).join(
            maximos,
            (ReferenceRate.series == maximos.c.series)
            & (ReferenceRate.as_of == maximos.c.as_of),
        )
    ).scalars().all()

    return {
        fila.series: RateReading(
            series=fila.series,
            label=BY_KEY[fila.series].label if fila.series in BY_KEY else fila.series,
            value=fila.value,
            as_of=fila.as_of,
            unit=fila.unit,
            nature=BY_KEY[fila.series].nature if fila.series in BY_KEY else "nominal",
        )
        for fila in filas
    }


def real_rate(nominal_pct: float, inflation_pct: float) -> float:
    """Tasa real por Fisher exacto, no por la resta.

    Con tasas de dos dígitos la aproximación «nominal menos inflación» se
    queda corta de forma apreciable: 12,3% con 6,24% de inflación da 5,70% de
    tasa real, no 6,06%. La diferencia de 36 puntos básicos es el 6% del
    rendimiento real, y en renta fija eso es mucho.
    """
    return ((1 + nominal_pct / 100) / (1 + inflation_pct / 100) - 1) * 100


# ---------------------------------------------------------------------------
# Tasas de CDT por banco
# ---------------------------------------------------------------------------


def refresh_cdt_offers(
    db: Session, client: DatosGovClient | None = None
) -> tuple[int, list[str]]:
    """Trae las emisiones de CDT recientes. Devuelve (filas, avisos)."""
    client = client or DatosGovClient()
    try:
        ofertas = client.fetch_offers()
    except ProviderError as exc:
        logger.warning("No se pudieron traer las tasas de CDT: %s", exc)
        return 0, [f"Tasas de CDT de datos.gov.co no disponibles: {exc}"]

    if not ofertas:
        return 0, ["datos.gov.co no devolvió ninguna emisión de CDT"]

    ahora = dt.datetime.now(dt.UTC)
    payload = [
        {
            "entity": o.entity,
            "term_label": o.term_label,
            "rate_pct": o.rate_pct,
            "amount": o.amount,
            "as_of": o.as_of,
            "fetched_at": ahora,
        }
        for o in ofertas
    ]
    escritas = upsert(
        db,
        CdtOffer.__table__,
        payload,
        index_elements=["entity", "term_label"],
        update_columns=["rate_pct", "amount", "as_of", "fetched_at"],
    )
    db.commit()
    return escritas, []


@dataclass(frozen=True)
class CdtComparison:
    """Qué paga el mercado a un plazo, y qué paga un banco concreto.

    Las dos cifras hacen falta. La mejor del mercado sin la del banco propio
    no dice si hay que moverse; la del banco propio sin la mejor no dice
    cuánto se está dejando encima de la mesa.
    """

    days: int
    best_rate_pct: float
    best_entity: str
    median_rate_pct: float
    issuer_rate_pct: float | None
    issuer_name: str | None
    # QUÉ CUBOS se usaron. La fuente no publica días sino tramos con nombre, y
    # los tramos no son iguales de finos: «A 360 DIAS» es exactamente 360,
    # mientras que «SUPERIORES A 360 DIAS» va de 361 a cualquier cosa. Una
    # mediana sobre el segundo mezcla un CDT a un año con uno a cinco, y sin
    # decir cuál se usó no hay forma de saber si la comparación es fina o
    # gruesa.
    term_labels: tuple[str, ...]
    offers_considered: int
    as_of: dt.date
    caveat: str


def compare_cdt(
    db: Session, *, days: int, issuer: str | None = None
) -> CdtComparison | None:
    """Compara un plazo contra lo que pagó el mercado.

    El plazo se traduce a los rangos que publica la fuente: pedir 365 días
    recoge «A 360 DIAS» y «SUPERIORES A 360 DIAS», que es lo comparable. Fuera
    de esos rangos no hay respuesta, y decirlo es mejor que estirar el plazo
    más cercano.
    """
    plazos = [nombre for nombre, (lo, hi) in PLAZOS.items() if lo <= days <= hi]
    if not plazos:
        return None

    filas = list(
        db.scalars(select(CdtOffer).where(CdtOffer.term_label.in_(plazos))).all()
    )
    if not filas:
        return None

    tasas = sorted(f.rate_pct for f in filas)
    mejor = max(filas, key=lambda f: f.rate_pct)

    propia = None
    nombre_emisor = None
    if issuer:
        objetivo = issuer.strip().lower()
        candidatas = [f for f in filas if objetivo in f.entity.lower()]
        if candidatas:
            elegida = max(candidatas, key=lambda f: f.rate_pct)
            propia = elegida.rate_pct
            nombre_emisor = elegida.entity

    return CdtComparison(
        days=days,
        # Se redondean AQUÍ: la mediana de dos tasas sale con la cola de
        # coma flotante (11.204999999999998) y la ficha la imprime tal cual.
        best_rate_pct=round(mejor.rate_pct, 2),
        best_entity=mejor.entity,
        median_rate_pct=round(statistics.median(tasas), 2),
        issuer_rate_pct=None if propia is None else round(propia, 2),
        issuer_name=nombre_emisor,
        term_labels=tuple(sorted({f.term_label for f in filas})),
        offers_considered=len(filas),
        as_of=max(f.as_of for f in filas),
        caveat=(
            "Son tasas efectivamente PACTADAS y publicadas por la "
            "Superintendencia Financiera, no las de la vitrina: nadie publica "
            "el descuento que hizo por un depósito grande. Tómalas como "
            "referencia de lo que se está pagando, no como una oferta."
        ),
    )
