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
Es la MAYOR entre la caída máxima observada y un SUELO por CLASE de activo.

El suelo existe porque lo observado depende de la ventana: un activo tranquilo
en el tramo guardado puede haber caído mucho más justo antes, y usar solo lo
observado le daría un peso enorme precisamente cuando menos deberías fiarte de
la calma.

LOS SUELOS ESTÁN MEDIDOS, no supuestos. Son la mediana de la caída máxima
observada en cinco años dentro de cada clase, sobre los 488 activos del
universo con histórico suficiente (20-09-2026):

    clase              n    mediana     p90     peor caso
    acciones         266      42,6%   73,1%    RIVN  95%
    fondos acciones  124      30,4%   47,9%    TAN   74%
    renta fija        31      15,2%   29,4%    TLT   44%
    materias primas   20      30,1%   73,6%    UNG   93%
    cripto            25      85,3%   98,6%    ARB  100%

La lectura del suelo es: «si lo que has observado es menos de lo que sufrió un
miembro típico de su clase, asume lo típico». Sigue siendo un juicio -elegir la
mediana y no el p90 lo es-, pero es un juicio sobre cifras medidas y no sobre
una intuición, y está a la vista.

El caso del cripto es el que más cambia: con el suelo de una acción (35%) un
2% de presupuesto daba un 5,7% de la cartera; con el 85% medido da un 2,4%.
Antes de este cambio, `stress_floor` solo distinguía «fondo» de «acción», así
que una cripto se dimensionaba como si fuera una empresa.

LO QUE ESTO NO HACE
===================
No optimiza la cartera, no mira correlaciones entre posiciones y no sabe cuánto
efectivo tienes. Es un techo razonable para una posición, no una orden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from app.services.asset_class import AssetClass

# Suelos de la caída de estrés (fracción). Ver el docstring del módulo: son la
# mediana medida de cada clase, redondeada.
STRESS_FLOOR_BY_CLASS: dict[AssetClass, float] = {
    AssetClass.ACCION: 0.40,
    AssetClass.FONDO_ACCIONES: 0.30,
    AssetClass.RENTA_FIJA: 0.15,
    AssetClass.MATERIAS_PRIMAS: 0.30,
    AssetClass.CRIPTO: 0.85,
    AssetClass.DERIVADO: 0.45,
}

# Para lo que no se ha podido clasificar. Es el más severo de los comunes a
# propósito: no saber qué es algo no puede salir más barato que saberlo.
STRESS_FLOOR_UNKNOWN = 0.50

# Con artículo incluido: el texto las inserta tal cual, y tenerlo aquí evita
# el «un un fondo de acciones» que salía al anteponerlo en la plantilla.
CLASS_DESCRIPTION: dict[AssetClass, str] = {
    AssetClass.ACCION: "una acción individual",
    AssetClass.FONDO_ACCIONES: "un fondo de acciones",
    AssetClass.RENTA_FIJA: "un fondo de renta fija",
    AssetClass.MATERIAS_PRIMAS: "una cesta de materias primas",
    AssetClass.CRIPTO: "una cripto",
    AssetClass.DERIVADO: "un derivado",
    AssetClass.DESCONOCIDO: "un activo sin clasificar",
}


@dataclass(frozen=True)
class SizingResult:
    """Resultado del cálculo. Porcentajes en escala 0-100; importes en la divisa base."""

    risk_budget_pct: float
    max_position_pct: float
    # La clase con la que se eligió el suelo. Viaja en el resultado porque
    # quien recalcule con otro capital tiene que usar la MISMA, y deducirla
    # del suelo -como se hacía- deja de funcionar en cuanto dos clases
    # comparten valor.
    asset_class: str
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
    # A QUÉ se aplica este techo. Ver el docstring de `size_position`.
    applies_to: str = "single_position"
    notes: list[str] = field(default_factory=list)


def stress_floor(asset_class: AssetClass) -> float:
    return STRESS_FLOOR_BY_CLASS.get(asset_class, STRESS_FLOOR_UNKNOWN)


def size_position(
    *,
    max_drawdown: float | None,
    asset_class: AssetClass,
    risk_budget_pct: float,
    max_position_pct: float,
    current_weight_pct: float = 0.0,
    capital: Decimal | None = None,
    currency_differs: bool = False,
    is_diversified: bool = False,
) -> SizingResult:
    """Peso objetivo de una posición y cuánto AÑADIR dado lo que ya tienes.

    `max_drawdown` es una fracción positiva (0,35 = cayó 35%) o None si no hay
    histórico. `capital` es el total de la cartera en divisa base; sin él solo
    se devuelven porcentajes.

    `is_diversified` CAMBIA LO QUE SIGNIFICA LA CIFRA, no la cifra.
    ================================================================
    El método reparte un presupuesto de riesgo entre APUESTAS: cuánto puedes
    poner en una cosa que puede caer sola. Un fondo mundial no es una apuesta,
    es la base sobre la que se ponen las apuestas, y decirle a alguien que su
    VT no puede pasar del 6,7% de la cartera es decirle que deje el 93% en
    efectivo. Es justo al revés de lo razonable.

    El número sigue siendo correcto para lo que mide -si tratas VT como una
    posición más entre muchas, 6,7% es el techo- pero la pregunta para un
    núcleo es otra y se contesta en otro sitio: el plan de asignación, donde
    decides cuánto quieres en cada clase. Por eso aquí solo cambia `applies_to`
    y la nota; inventar un techo distinto para los fondos amplios sería
    fabricar un número que no mide nada.
    """
    if risk_budget_pct <= 0 or max_position_pct <= 0:
        raise ValueError("El presupuesto de riesgo y el tope deben ser positivos")

    floor = stress_floor(asset_class)
    descripcion = CLASS_DESCRIPTION.get(asset_class, "este activo")
    notes: list[str] = []

    if max_drawdown is None or max_drawdown < 0:
        stress = floor
        source = "floor"
        observed = None
        notes.append(
            "No hay histórico suficiente para medir su peor caída: se usa el suelo "
            f"de {floor * 100:.0f}% para {descripcion}."
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
            f"Su peor caída medida fue {max_drawdown * 100:.0f}%, por debajo de lo que "
            f"sufrió {descripcion} típica en cinco años: se usa el suelo de "
            f"{floor * 100:.0f}% porque una calma reciente no garantiza nada."
        )

    by_risk = risk_budget_pct / (stress * 100) * 100  # % de la cartera
    # Se redondea AQUÍ y no al imprimir: `6.666666666666667%` viaja tal cual
    # por la API y lo consume también el asesor, que lo repite sin formatear.
    target = round(min(by_risk, max_position_pct), 2)
    binding = "risk" if by_risk <= max_position_pct else "cap"

    add = round(max(0.0, target - current_weight_pct), 2)
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
    if is_diversified:
        notes.append(
            f"OJO: esto es un techo para una posición SUELTA, y esto es un "
            f"instrumento diversificado. Si lo estás usando como NÚCLEO de la "
            f"cartera, el {target:.1f}% no aplica: un núcleo no se dimensiona "
            f"por presupuesto de riesgo sino por tu plan de asignación, en la "
            f"pestaña Plan. Aquí se deja la cifra por si lo tratas como una "
            f"posición más entre muchas."
        )
    else:
        notes.append(
            "Es un techo razonable para UNA posición, no una orden: no considera "
            "cuánto efectivo tienes ni cómo se mueve junto con el resto de tu "
            "cartera."
        )

    result = dict(
        risk_budget_pct=risk_budget_pct,
        max_position_pct=max_position_pct,
        asset_class=asset_class.value,
        applies_to="core_or_single" if is_diversified else "single_position",
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
