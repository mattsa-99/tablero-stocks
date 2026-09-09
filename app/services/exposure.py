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
"""

from __future__ import annotations

from app.models import Asset, AssetType

BROAD_EQUITY = "Diversificado"
FIXED_INCOME = "Renta fija"
COMMODITIES = "Materias primas"
UNKNOWN = "Desconocido"

# ETFs sectoriales: se comportan igual que una acción de ese sector, así que se
# mapean a él y compiten en el mismo cubo.
SECTOR_ETFS: dict[str, str] = {
    "XLE": "Energy",
    "XLF": "Financial Services",
    "XLV": "Healthcare",
    "XLK": "Technology",
    "XLI": "Industrials",
    "XLY": "Consumer Cyclical",
    "XLP": "Consumer Defensive",
    "XLU": "Utilities",
    "XLB": "Basic Materials",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
    "VNQ": "Real Estate",
    "SMH": "Technology",
    "IBB": "Healthcare",
}

# ETFs de mercado amplio: son diversificación en sí mismos.
BROAD_ETFS: frozenset[str] = frozenset({
    "SPY", "VOO", "IVV", "VTI", "QQQ", "IWM", "DIA", "RSP",
    "EEM", "VWO", "EFA", "VEA", "VXUS", "ACWI", "VT", "SCHB",
})

# Renta fija: diversifica frente a renta variable, que es distinto de
# diversificar entre sectores de renta variable.
BOND_ETFS: frozenset[str] = frozenset({
    "TLT", "IEF", "SHY", "AGG", "BND", "LQD", "HYG", "TIP", "BNDX", "EMB",
})

COMMODITY_ETFS: frozenset[str] = frozenset({"GLD", "IAU", "SLV", "USO", "DBC", "PDBC"})


def exposure_bucket(asset: Asset) -> str:
    """Cubo de exposición de un activo, para el cálculo de diversificación.

    El orden de las comprobaciones importa: un símbolo listado explícitamente
    gana sobre cualquier heurística, porque la heurística acierta en general y
    falla justo en los casos raros.
    """
    symbol = (asset.symbol or "").upper()

    if symbol in SECTOR_ETFS:
        return SECTOR_ETFS[symbol]
    if symbol in BOND_ETFS:
        return FIXED_INCOME
    if symbol in COMMODITY_ETFS or symbol in BROAD_ETFS:
        return COMMODITIES if symbol in COMMODITY_ETFS else BROAD_EQUITY

    # Futuros continuos de Yahoo: GC=F (oro), CL=F (crudo)...
    if symbol.endswith("=F"):
        return COMMODITIES

    if asset.sector:
        return asset.sector

    # ETF o fondo sin sector y fuera de las listas: lo más probable es que sea
    # amplio, porque un ETF concentrado normalmente SÍ trae sector. Se anota
    # como supuesto en `is_assumed_bucket` para poder declararlo en la interfaz.
    if asset.asset_type in (AssetType.ETF, AssetType.FUND):
        return BROAD_EQUITY

    return UNKNOWN


def is_assumed_bucket(asset: Asset) -> bool:
    """True si el cubo salió de una heurística y no de un dato explícito.

    La interfaz debe poder decirlo: un supuesto presentado como hecho es peor
    que un dato ausente.
    """
    symbol = (asset.symbol or "").upper()
    if symbol in SECTOR_ETFS or symbol in BOND_ETFS or symbol in COMMODITY_ETFS:
        return False
    if symbol in BROAD_ETFS or symbol.endswith("=F"):
        return False
    if asset.sector:
        return False
    return asset.asset_type in (AssetType.ETF, AssetType.FUND)


def bucket_label(bucket: str) -> str:
    """Etiqueta legible. Los sectores GICS se dejan tal cual llegan de Yahoo."""
    return bucket
