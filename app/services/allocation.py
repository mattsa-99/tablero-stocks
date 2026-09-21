"""Plan de asignación entre clases: peso actual frente al que quieres.

QUÉ RESPONDE Y QUÉ NO
=====================
Responde «¿me he desviado de mi plan, y hacia dónde?». No responde «¿qué
compro?»: eso es el ranking, y el ranking ordena DENTRO de una clase porque
comparar una acción con un bono no es una decisión que un score pueda tomar.

Las dos preguntas son complementarias y el orden importa: primero se decide el
reparto entre clases -aquí-, y solo después se elige qué comprar dentro de la
clase que está por debajo.

REBALANCEO CON APORTES, NO CON VENTAS
=====================================
La sugerencia dirige el dinero NUEVO a la clase más rezagada. Vender para
rebalancear realiza ganancias, paga impuestos y comisiones, y en una cartera
en formación casi nunca hace falta: basta con dónde se pone lo siguiente.
Por eso `suggest_contribution` no propone ventas ni cuando una clase se pasa
de banda: dice cuánto se ha pasado y deja la decisión.

LA BANDA ES LO QUE EVITA EL RUIDO
=================================
Sin tolerancia, cualquier movimiento del mercado «obliga» a rebalancear. La
banda define cuándo una desviación deja de ser ruido y empieza a ser una
decisión. Cinco puntos por defecto es un juicio declarado, no el resultado de
una optimización.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import InvalidAllocationPlan
from app.models import AllocationTarget, Portfolio
from app.schemas.position import PortfolioSummary
from app.services.asset_class import CLASS_LABEL, AssetClass

HUNDRED = Decimal("100")
DEFAULT_BAND_PCT = Decimal("5")

# Clases sobre las que tiene sentido fijar un objetivo. `derivado` queda fuera
# porque no es accionable con una cartera normal, y `desconocido` porque no es
# una decisión sino una carencia de datos: ponerle un objetivo sería planificar
# sobre algo que no se sabe qué es.
PLANNABLE_CLASSES: tuple[AssetClass, ...] = (
    AssetClass.FONDO_ACCIONES,
    AssetClass.ACCION,
    AssetClass.RENTA_FIJA,
    AssetClass.MATERIAS_PRIMAS,
    AssetClass.CRIPTO,
)


@dataclass(frozen=True)
class ClassPosition:
    """Una clase: lo que tienes, lo que querías y la diferencia."""

    asset_class: str
    label: str
    current_pct: Decimal
    current_amount: Decimal
    target_pct: Decimal | None
    band_pct: Decimal | None
    drift_pct: Decimal | None       # actual - objetivo, en puntos
    status: str                     # "dentro" | "por_debajo" | "por_encima" | "sin_plan"
    gap_amount: Decimal | None      # cuánto falta para llegar al objetivo
    notes: str | None = None


@dataclass
class AllocationReport:
    positions: list[ClassPosition]
    total_value: Decimal | None
    planned_pct: Decimal            # cuánto suman los objetivos declarados
    has_plan: bool
    warnings: list[str] = field(default_factory=list)

    @property
    def out_of_band(self) -> list[ClassPosition]:
        return [p for p in self.positions if p.status in ("por_debajo", "por_encima")]


def get_targets(db: Session, portfolio: Portfolio) -> list[AllocationTarget]:
    return list(
        db.scalars(
            select(AllocationTarget)
            .where(AllocationTarget.portfolio_id == portfolio.id)
            .order_by(AllocationTarget.asset_class)
        ).all()
    )


def set_targets(
    db: Session, portfolio: Portfolio, targets: dict[str, tuple[Decimal, Decimal]]
) -> list[AllocationTarget]:
    """Reemplaza el plan entero. `targets` es {clase: (objetivo, banda)}.

    TODO O NADA, y con la suma comprobada. Un plan que sume 90% no es un plan
    incompleto: es uno en el que el 10% restante no está decidido, y presentar
    porcentajes sobre esa base haría que los desvíos midieran contra algo que
    el usuario no eligió. Se admite que sume menos de 100 solo si el usuario
    lo declara dejando clases fuera, pero nunca más de 100.
    """
    validas = {c.value for c in PLANNABLE_CLASSES}
    total = Decimal(0)
    for clase, (objetivo, banda) in targets.items():
        if clase not in validas:
            raise InvalidAllocationPlan(
                f"«{clase}» no admite un objetivo de asignación. "
                f"Clases planificables: {', '.join(sorted(validas))}."
            )
        if objetivo < 0 or objetivo > HUNDRED:
            raise InvalidAllocationPlan(f"El objetivo de «{clase}» debe estar entre 0 y 100.")
        if banda < 0 or banda > HUNDRED:
            raise InvalidAllocationPlan(f"La banda de «{clase}» debe estar entre 0 y 100.")
        total += objetivo

    if total > HUNDRED:
        raise InvalidAllocationPlan(
            f"Los objetivos suman {total}%, más de 100. Un plan que se pasa de "
            f"100 no se puede cumplir: ajústalo antes de guardarlo."
        )

    existentes = {t.asset_class: t for t in get_targets(db, portfolio)}
    for clase, fila in existentes.items():
        if clase not in targets:
            db.delete(fila)

    for clase, (objetivo, banda) in targets.items():
        fila = existentes.get(clase)
        if fila is None:
            fila = AllocationTarget(portfolio_id=portfolio.id, asset_class=clase)
            db.add(fila)
        fila.target_pct = objetivo
        fila.band_pct = banda

    db.commit()
    return get_targets(db, portfolio)


def build_report(
    db: Session, portfolio: Portfolio, summary: PortfolioSummary
) -> AllocationReport:
    """Peso actual por clase frente al plan.

    El denominador es `total_value` -posiciones MÁS caja-, no solo las
    posiciones. Si fuera solo las posiciones, quien tiene la mitad en efectivo
    esperando a entrar vería un 100% en renta variable y creería estar
    invertido del todo.
    """
    objetivos = {t.asset_class: t for t in get_targets(db, portfolio)}
    warnings: list[str] = []

    por_clase: dict[str, Decimal] = {}
    sin_valorar = 0
    for posicion in summary.positions:
        if posicion.market_value is None:
            sin_valorar += 1
            continue
        por_clase[posicion.asset_class] = (
            por_clase.get(posicion.asset_class, Decimal(0)) + posicion.market_value
        )

    if sin_valorar:
        warnings.append(
            f"{sin_valorar} posición(es) sin precio o sin tipo de cambio no entran "
            f"en el reparto: los porcentajes son sobre lo que sí se pudo valorar."
        )

    # EL DENOMINADOR, y por qué no siempre es `total_value`.
    #
    # Lo correcto es posiciones MÁS caja: quien tiene la mitad en efectivo
    # esperando a entrar no está invertido del todo, y con solo las posiciones
    # vería un 100% en renta variable justo cuando más lejos está del plan.
    #
    # Pero la caja de este sistema PUEDE SER NEGATIVA, y es deliberado: permite
    # cargar un histórico ya existente sin registrar los depósitos previos. Con
    # caja negativa, `total_value` puede salir cero o menos, y dividir por eso
    # daría porcentajes infinitos o con el signo cambiado. En ese caso se
    # reparte sobre lo invertido y se dice por qué: es menos correcto, pero es
    # lo único que se puede afirmar con los datos que hay.
    invertido = sum(por_clase.values(), Decimal(0))
    total = summary.total_value
    if total is None or total <= 0:
        if summary.cash_balance < 0:
            warnings.append(
                f"Tu caja registrada es negativa ({summary.cash_balance}): faltan "
                f"los depósitos. Los porcentajes se reparten sobre lo invertido, "
                f"no sobre el patrimonio, así que el efectivo disponible no cuenta."
            )
        total = invertido

    clases = sorted(set(por_clase) | set(objetivos))
    filas: list[ClassPosition] = []
    for clase in clases:
        importe = por_clase.get(clase, Decimal(0))
        actual = (
            (importe / total * HUNDRED) if total and total > 0 else Decimal(0)
        )
        objetivo = objetivos.get(clase)

        if objetivo is None:
            filas.append(ClassPosition(
                asset_class=clase,
                label=CLASS_LABEL.get(_as_class(clase), clase),
                current_pct=_round(actual),
                current_amount=importe,
                target_pct=None, band_pct=None, drift_pct=None,
                status="sin_plan", gap_amount=None,
            ))
            continue

        desvio = actual - objetivo.target_pct
        if abs(desvio) <= objetivo.band_pct:
            estado = "dentro"
        else:
            estado = "por_debajo" if desvio < 0 else "por_encima"

        hueco = (
            (objetivo.target_pct - actual) / HUNDRED * total
            if total and total > 0
            else None
        )
        filas.append(ClassPosition(
            asset_class=clase,
            label=CLASS_LABEL.get(_as_class(clase), clase),
            current_pct=_round(actual),
            current_amount=importe,
            target_pct=objetivo.target_pct,
            band_pct=objetivo.band_pct,
            drift_pct=_round(desvio),
            status=estado,
            gap_amount=None if hueco is None else _round(hueco),
            notes=objetivo.notes,
        ))

    planificado = sum((t.target_pct for t in objetivos.values()), Decimal(0))
    if objetivos and planificado < HUNDRED:
        warnings.append(
            f"Tu plan cubre el {planificado}% de la cartera. El {HUNDRED - planificado}% "
            f"restante no tiene objetivo: los desvíos se miden solo sobre lo que sí "
            f"declaraste."
        )

    return AllocationReport(
        positions=filas,
        total_value=total,
        planned_pct=planificado,
        has_plan=bool(objetivos),
        warnings=warnings,
    )


def suggest_contribution(report: AllocationReport, amount: Decimal) -> list[ClassPosition]:
    """A qué clases dirigir un aporte nuevo, de la más rezagada a la menos.

    No propone vender. Rebalancear con aportes evita realizar ganancias y
    pagar comisiones, y en una cartera en formación suele bastar.
    """
    if amount <= 0 or not report.has_plan:
        return []
    rezagadas = [p for p in report.positions if p.gap_amount and p.gap_amount > 0]
    return sorted(rezagadas, key=lambda p: p.gap_amount or Decimal(0), reverse=True)


def _as_class(value: str) -> AssetClass:
    try:
        return AssetClass(value)
    except ValueError:
        return AssetClass.DESCONOCIDO


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))
