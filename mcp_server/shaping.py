"""Da forma a las respuestas para que las lea un modelo, sin inventar nada.

DOS REGLAS
==========
1. **Los Decimal viajan como texto**, igual que en la API. Un modelo que
   recibe 2217950.1234567 escribe eso en su respuesta; recibiendo "2217950.12"
   escribe lo que el usuario ve en pantalla.
2. **La frescura va DENTRO de cada respuesta, no en una herramienta aparte.**
   Si hay que pedirla por separado, el modelo no la pide: la regla de «si los
   datos no bastan, dilo» solo se cumple si el dato de cuán viejos son llega
   junto con ellos.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any


def money(value: Decimal | float | None, places: int = 2) -> str | None:
    """Importe como texto, redondeado a lo que se muestra en pantalla.

    Normaliza el cero con signo. Las cotizaciones llegan con ruido de coma
    flotante -IVV cotiza a 764.919982910156, no a 764,92- así que una compra
    registrada al precio del día deja un P&L de −0,0000170898, que se
    redondeaba a «-0.00». Un modelo que lee eso escribe «estás perdiendo» sobre
    una cifra que es cero. Es el mismo fallo que ya se corrigió en la interfaz,
    y aquí es peor: el frontend lo muestra y el modelo lo AFIRMA.
    """
    if value is None:
        return None
    quantum = Decimal(1).scaleb(-places)
    redondeado = Decimal(str(value)).quantize(quantum)
    return str(redondeado + 0) if redondeado != 0 else str(abs(redondeado))


def pct(value: float | Decimal | None, places: int = 2) -> float | None:
    if value is None:
        return None
    return round(float(value), places)


def day(value: dt.date | dt.datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def compact(payload: Any) -> Any:
    """Quita None y colecciones vacías, en profundidad.

    Un modelo que ve `"fcf_yield": null` junto a otras treinta claves nulas
    gasta atención en ruido. Lo ausente se omite; cuando la ausencia SIGNIFICA
    algo -no hay precio, no se pudo calificar- la herramienta lo dice con un
    texto explícito en vez de con un null.
    """
    if isinstance(payload, dict):
        limpio = {k: compact(v) for k, v in payload.items()}
        return {k: v for k, v in limpio.items() if v is not None and v != [] and v != {}}
    if isinstance(payload, list):
        return [compact(v) for v in payload]
    if isinstance(payload, Decimal):
        return str(payload)
    if isinstance(payload, (dt.date, dt.datetime)):
        return payload.isoformat()
    return payload


# Claves que NO se redondean: su precisión es significativa. Una cantidad de
# cripto puede tener ocho decimales de verdad, y un tipo COP->USD vale
# 0,000314 -redondeado a dos decimales sería CERO y multiplicaría por nada-.
_KEEP_PRECISION = frozenset({"quantity", "fx_rate_to_base", "fx_rate", "rate"})


def round_amounts(payload: Any, places: int = 2, _key: str | None = None) -> Any:
    """Redondea los importes que viajan como texto, en profundidad.

    Los servicios devuelven `Decimal` exacto -es su razón de ser- y Pydantic
    los serializa enteros: `market_value` sale como "959.589981079101511" y un
    P&L nulo como "-0.000018920898489". Un modelo que lee eso escribe «estás
    perdiendo» sobre una cifra que es cero, y además reproduce dieciocho
    decimales que el usuario nunca ve en pantalla.

    Redondear aquí y no en el servicio es deliberado: la exactitud del ledger
    no se toca, solo su presentación al asesor. Es la misma frontera que el
    frontend cruza con `window.fmt`.
    """
    if isinstance(payload, dict):
        return {k: round_amounts(v, places, k) for k, v in payload.items()}
    if isinstance(payload, list):
        return [round_amounts(v, places, _key) for v in payload]
    if _key in _KEEP_PRECISION:
        return payload
    if isinstance(payload, Decimal):
        return money(payload, places)
    if isinstance(payload, str):
        try:
            return money(Decimal(payload), places)
        except (ArithmeticError, ValueError):
            return payload
    return payload
