"""Tamaño de posición: cuánto poner en UNA empresa.

LA IDEA, EN UNA FRASE
=====================
No se decide cuánto comprar por cuánto «te gusta» la empresa, sino por cuánto
estás dispuesto a perder si le pasa lo peor que ya le ha pasado.

    peso = presupuesto de riesgo / caída de estrés        (con un tope)

Con un presupuesto del 2% y una acción que ya llegó a caer 40%, el peso es
2% / 40% = 5% de la cartera: si repite su peor caída, pierdes 2% del total.

CÓMO SE ELIGE LA «CAÍDA DE ESTRÉS»
==================================
Es la MAYOR entre la caída máxima observada y un SUELO por tipo de activo.

El suelo existe porque el histórico guardado es corto (`price_history_days`,
~1 año): una acción tranquila ese año puede haber caído 50% dos años atrás, y
usar solo lo observado le daría un peso enorme justo cuando menos deberías
fiarte de la calma. Los suelos (35% acción individual, 25% fondo) son un
juicio, están a la vista y se declaran en las notas.

LO QUE ESTO NO HACE
===================
No optimiza la cartera, no mira correlaciones entre posiciones y no sabe cuánto
efectivo tienes. Es un techo razonable para una posición, no una orden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

# Suelos de la caída de estrés (fracción). Ver el docstring del módulo.
STRESS_FLOOR_STOCK = 0.35
STRESS_FLOOR_FUND = 0.25


@dataclass(frozen=True)
class SizingResult:
    """Resultado del cálculo. Porcentajes en escala 0-100; importes en la divisa base."""

    risk_budget_pct: float
    max_position_pct: float
    stress_loss_pct: float
    stress_source: str  # "observed" | "floor"
    observed_drawdown_pct: float | None
    target_pct: float
    binding: str  # "risk" | "cap"
    current_pct: float
    add_pct: float
    capital: Decimal | None = None
    target_amount: Decimal | None = None
    add_amount: Decimal | None = None
    loss_if_repeats_amount: Decimal | None = None
    loss_if_repeats_pct_of_capital: float = 0.0
    notes: list[str] = field(default_factory=list)


def stress_floor(is_fund: bool) -> float:
    return STRESS_FLOOR_FUND if is_fund else STRESS_FLOOR_STOCK


def size_position(
    *,
    max_drawdown: float | None,
    is_fund: bool,
    risk_budget_pct: float,
    max_position_pct: float,
    current_weight_pct: float = 0.0,
    capital: Decimal | None = None,
    currency_differs: bool = False,
) -> SizingResult:
    """Peso objetivo de una posición y cuánto AÑADIR dado lo que ya tienes.

    `max_drawdown` es una fracción positiva (0,35 = cayó 35%) o None si no hay
    histórico. `capital` es el total de la cartera en divisa base; sin él solo
    se devuelven porcentajes.
    """
    if risk_budget_pct <= 0 or max_position_pct <= 0:
        raise ValueError("El presupuesto de riesgo y el tope deben ser positivos")

    floor = stress_floor(is_fund)
    notes: list[str] = []

    if max_drawdown is None or max_drawdown < 0:
        stress = floor
        source = "floor"
        observed = None
        notes.append(
            "No hay histórico suficiente para medir su peor caída: se usa el suelo "
            f"de {floor * 100:.0f}% para {'un fondo' if is_fund else 'una acción individual'}."
        )
    elif max_drawdown >= floor:
        stress = max_drawdown
        source = "observed"
        observed = max_drawdown
    else:
        stress = floor
        source = "floor"
        observed = max_drawdown
        notes.append(
            f"Su peor caída medida fue {max_drawdown * 100:.0f}%, pero el histórico guardado "
            f"es corto (~1 año): se usa un suelo de {floor * 100:.0f}% porque una calma "
            "reciente no garantiza que no pueda caer más."
        )

    by_risk = risk_budget_pct / (stress * 100) * 100  # % de la cartera
    target = min(by_risk, max_position_pct)
    binding = "risk" if by_risk <= max_position_pct else "cap"

    add = max(0.0, target - current_weight_pct)
    if current_weight_pct >= target and current_weight_pct > 0:
        notes.append(
            f"Ya pesas {current_weight_pct:.1f}%, que alcanza o supera el objetivo: "
            "no conviene añadir más."
        )
    if currency_differs:
        notes.append(
            "Cotiza en otra divisa que tu cartera: además de la caída del precio, "
            "el tipo de cambio puede sumar o restar. El riesgo real puede ser mayor."
        )
    notes.append(
        "Es un techo razonable para UNA posición, no una orden: no considera cuánto "
        "efectivo tienes ni cómo se mueve junto con el resto de tu cartera."
    )

    result = dict(
        risk_budget_pct=risk_budget_pct,
        max_position_pct=max_position_pct,
        stress_loss_pct=stress * 100,
        stress_source=source,
        observed_drawdown_pct=None if observed is None else observed * 100,
        target_pct=target,
        binding=binding,
        current_pct=current_weight_pct,
        add_pct=add,
        notes=notes,
    )

    if capital is not None and capital > 0:
        factor = Decimal(str(round(target, 6))) / Decimal(100)
        add_factor = Decimal(str(round(add, 6))) / Decimal(100)
        target_amount = capital * factor
        add_amount = capital * add_factor
        worst = target_amount * Decimal(str(round(stress, 6)))
        result.update(
            capital=capital,
            target_amount=target_amount.quantize(Decimal("0.01")),
            add_amount=add_amount.quantize(Decimal("0.01")),
            loss_if_repeats_amount=worst.quantize(Decimal("0.01")),
            loss_if_repeats_pct_of_capital=target * stress,
        )
    else:
        result["loss_if_repeats_pct_of_capital"] = target * stress

    return SizingResult(**result)
