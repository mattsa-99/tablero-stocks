"""Excepciones de dominio.

Se traducen a códigos HTTP en un único punto (`app/main.py`), para que los
services nunca importen FastAPI ni conozcan el transporte.
"""

from __future__ import annotations


class TableroError(Exception):
    """Raíz de todos los errores de dominio."""


class NotFoundError(TableroError):
    """El recurso solicitado no existe. -> 404"""


class DuplicateError(TableroError):
    """Violación de una restricción de unicidad. -> 409"""


class InvalidLedgerOperation(TableroError):
    """La operación dejaría el ledger en un estado imposible. -> 422

    Caso principal: vender más títulos de los disponibles en esa fecha. No se
    permiten posiciones cortas en la v1.
    """


class InsufficientUniverse(TableroError):
    """No hay candidatos suficientes para un ranking con sentido. -> 422

    Los rangos percentiles sobre menos de 5 candidatos son ruido: con n=2 los
    scores son 25 y 75 sin importar los valores subyacentes.
    """


# ----------------------------------------------------------------------
# Proveedores externos
# ----------------------------------------------------------------------


class ProviderError(TableroError):
    """Raíz de los fallos de datos externos.

    NINGUNA de estas debe traducirse en un 5xx en los endpoints de lectura: el
    portafolio se sirve igualmente con el último dato conocido y un aviso.
    """


class SymbolNotFound(ProviderError):
    """El ticker no existe en el proveedor. -> 422 al dar de alta"""


class ProviderRateLimited(ProviderError):
    """El proveedor está limitando las peticiones. Activa backoff."""


class ProviderUnavailable(ProviderError):
    """Timeout, error de red o respuesta ilegible."""


class ProviderUnreachable(ProviderUnavailable):
    """No se llegó a hablar con el proveedor: DNS, conexión o timeout.

    Subclase y no hermana de `ProviderUnavailable` para que todo lo que hoy
    la captura siga funcionando. La distinción importa en un solo sitio, y es
    el que evita el fallo más caro de este pipeline: un fallo de CONEXIÓN no
    dice nada sobre el símbolo que se pidió, así que no puede apuntarse
    contra él.

    Medido en el log del agente de launchd: 1.282 fallos de DNS frente a 23
    de rate limit. Con el portátil suspendido a mitad de sincronización, los
    494 símbolos del universo quedaban marcados como fallidos y en backoff
    exponencial individual por una red que se cayó."""


class MarketDataUnavailable(ProviderError):
    """No hay dato de mercado utilizable para el símbolo solicitado."""
