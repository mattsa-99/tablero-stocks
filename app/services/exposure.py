"""Cubos de exposición para medir diversificación.

CORRIGE UN FALLO DEL DISEÑO ORIGINAL. Antes la diversificación se medía solo
por sector GICS, y Yahoo no devuelve sector para los ETFs. El resultado era que
SPY -lo más diversificado que se puede comprar- recibía el valor neutro (50)
mientras cualquier acción suelta recibía 100. El sistema desaconsejaba
sistemáticamente los instrumentos más diversificadores.

La causa de fondo era usar el sector como única dimensión. La ausencia de
sector en un ETF no es un hueco de datos: es *porque abarca muchos*.

Modelo actual: cada activo cae en un CUBO DE EXPOSICIÓN, que puede ser un
sector GICS o una clase de activo. Es más correcto además por otra razón: el
oro diversifica una cartera de acciones mucho más que pasar de tecnología a
salud, y con sectores puros esa diferencia era invisible.

DE LISTAS DE SÍMBOLOS A DATOS DEL PROVEEDOR
===========================================
La primera versión decidía el cubo con tres listas escritas a mano
(`BROAD_ETFS`, `BOND_ETFS`, `COMMODITY_ETFS`) y suponía «Diversificado» para
todo lo demás. Medido sobre la base real: **136 de 490 activos (27,8%) caían en
«Diversificado» por suposición**, incluidos fondos de deuda pública, cestas de
materias primas y ETFs de bitcoin. Una cartera de bonos contaba como renta
variable amplia y la diversificación medida era falsa.

Ahora el cubo se deriva de `services/asset_class.py`, que lee la categoría del
proveedor. Los supuestos bajaron de 136 a **1** (PHYS). Las listas sobreviven
solo como respaldo para lo que el proveedor no categoriza -los fondos de la
BVC, por ejemplo-, y ese respaldo se declara como supuesto.
"""

from __future__ import annotations

from app.models import Asset
from app.services.asset_class import (
    AssetClass,
    bucket_is_assumed,
    classify,
    sector_of_fund,
)

BROAD_EQUITY = "Diversificado"
FIXED_INCOME = "Renta fija"
COMMODITIES = "Materias primas"
CRYPTO = "Cripto"
DERIVATIVES = "Derivados"
UNKNOWN = "Desconocido"

_BY_CLASS: dict[AssetClass, str] = {
    AssetClass.RENTA_FIJA: FIXED_INCOME,
    AssetClass.MATERIAS_PRIMAS: COMMODITIES,
    AssetClass.CRIPTO: CRYPTO,
    # Un futuro del S&P no es una materia prima, y antes acababa en ese cubo
    # porque la única regla era el sufijo `=F`. Con seis futuros de índices y
    # dos de bonos dentro del universo, meterlos en «Materias primas» hacía que
    # el cubo afirmara algo falso.
    AssetClass.DERIVADO: DERIVATIVES,
    AssetClass.DESCONOCIDO: UNKNOWN,
}

# RESPALDO, no la vía principal. Solo se consulta cuando el proveedor no trae
# categoría: los fondos de la BVC (ICOLCAP.CL, GXTESCOL.CL) no la tienen. Un
# cubo resuelto por aquí se declara SUPUESTO.
FALLBACK_SECTOR_ETFS: dict[str, str] = {
    "XLE": "Energy", "XLF": "Financial Services", "XLV": "Healthcare",
    "XLK": "Technology", "XLI": "Industrials", "XLY": "Consumer Cyclical",
    "XLP": "Consumer Defensive", "XLU": "Utilities", "XLB": "Basic Materials",
    "XLRE": "Real Estate", "XLC": "Communication Services",
    "VNQ": "Real Estate", "SMH": "Technology", "IBB": "Healthcare",
}


def exposure_bucket(asset: Asset) -> str:
    """Cubo de exposición de un activo, para el cálculo de diversificación."""
    clasificacion = classify(asset)

    directo = _BY_CLASS.get(clasificacion.asset_class)
    if directo is not None:
        return directo

    if clasificacion.asset_class is AssetClass.ACCION:
        # Por construcción una ACCION tiene sector o industria: `classify` no
        # la reconocería como empresa sin ninguno de los dos.
        return asset.sector or asset.industry or UNKNOWN

    # Fondo de acciones: sectorial si su categoría lo dice, amplio si no.
    sector = sector_of_fund(asset) or FALLBACK_SECTOR_ETFS.get(
        (asset.symbol or "").upper()
    )
    return sector or BROAD_EQUITY


def is_assumed_bucket(asset: Asset) -> bool:
    """True si el cubo salió de una heurística y no de un dato explícito.

    La interfaz debe poder decirlo: un supuesto presentado como hecho es peor
    que un dato ausente.
    """
    if (asset.symbol or "").upper() in FALLBACK_SECTOR_ETFS and not asset.fund_category:
        return True
    return bucket_is_assumed(asset)


def bucket_label(bucket: str) -> str:
    """Etiqueta legible. Los sectores GICS se dejan tal cual llegan de Yahoo."""
    return bucket
