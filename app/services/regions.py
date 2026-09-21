"""Región de mercado de un activo, para filtrar el ranking de oportunidades.

LA REGIÓN ES EL DOMICILIO DE LA EMPRESA, NO LA BOLSA DONDE COTIZA
================================================================
`CIB` cotiza en Nueva York y es Bancolombia; `PBR` cotiza en Nueva York y es
Petrobras. Clasificarlos como "EE.UU." porque su bolsa es la NYSE haría el
filtro inútil: el universo se financia en USD y COP a propósito (ver
`scripts/candidates.FUNDING_CURRENCIES`), así que CASI TODO cotiza en bolsas
estadounidenses. Filtrar por bolsa devolvería ~95% en "EE.UU." y dejaría
"Europa" y "LatAm" vacíos.

Lo que el usuario quiere saber al filtrar por mercado es a qué economía se
expone, y eso lo da `Asset.country`, que viene del proveedor (`longName`,
`country` de Yahoo) y sí distingue el ADR de la empresa estadounidense.

Se deriva en cada petición en lugar de guardarse en una columna: es un dato
calculado a partir de otros que ya están, y guardarlo abriría la puerta a que
quede desincronizado cuando el proveedor corrija el país de una empresa.

LOS ETFs NO TIENEN PAÍS
=======================
Yahoo no devuelve `country` para un ETF, así que su geografía no se puede
derivar: hay que declararla. `REGION_BY_SYMBOL` lo hace SOLO para los ETFs de
país o región, que es donde la geografía es el propósito del producto. Un ETF
sectorial estadounidense (XLE) o de bonos del Tesoro (TLT) cae en US por su
regla de tipo, y uno de materias primas (GLD) o multipaís (VT) en GLOBAL.
"""

from __future__ import annotations

from enum import StrEnum

from app.models import Asset, AssetType


class MarketRegion(StrEnum):
    US = "US"
    COL = "COL"
    LATAM = "LATAM"
    EU = "EU"
    ASIA = "ASIA"
    GLOBAL = "GLOBAL"


REGION_LABEL: dict[MarketRegion, str] = {
    MarketRegion.US: "EE.UU.",
    MarketRegion.COL: "Colombia",
    MarketRegion.LATAM: "LatAm",
    MarketRegion.EU: "Europa",
    MarketRegion.ASIA: "Asia",
    MarketRegion.GLOBAL: "Global/ETFs",
}

# Orden de presentación: primero los mercados en que se opera de forma directa.
REGION_ORDER: tuple[MarketRegion, ...] = (
    MarketRegion.US,
    MarketRegion.COL,
    MarketRegion.LATAM,
    MarketRegion.EU,
    MarketRegion.ASIA,
    MarketRegion.GLOBAL,
)

# País del proveedor -> región. Colombia va aparte de LatAm a propósito: es el
# mercado local del usuario y mezclarla con Brasil o México perdería justo la
# distinción que le interesa.
_COUNTRY_REGION: dict[str, MarketRegion] = {
    "Colombia": MarketRegion.COL,
    # LatAm
    "Brazil": MarketRegion.LATAM,
    "Mexico": MarketRegion.LATAM,
    "Chile": MarketRegion.LATAM,
    "Argentina": MarketRegion.LATAM,
    "Peru": MarketRegion.LATAM,
    "Panama": MarketRegion.LATAM,
    "Uruguay": MarketRegion.LATAM,
    # Europa
    "United Kingdom": MarketRegion.EU,
    "Germany": MarketRegion.EU,
    "France": MarketRegion.EU,
    "Switzerland": MarketRegion.EU,
    "Netherlands": MarketRegion.EU,
    "Spain": MarketRegion.EU,
    "Italy": MarketRegion.EU,
    "Denmark": MarketRegion.EU,
    "Sweden": MarketRegion.EU,
    "Norway": MarketRegion.EU,
    "Finland": MarketRegion.EU,
    "Ireland": MarketRegion.EU,
    "Belgium": MarketRegion.EU,
    "Austria": MarketRegion.EU,
    "Portugal": MarketRegion.EU,
    "Luxembourg": MarketRegion.EU,
    "Poland": MarketRegion.EU,
    # Asia. Se mantiene ESTRICTO: Australia y Nueva Zelanda son Asia-Pacífico
    # en la jerga de los índices, pero no Asia, y meterlas aquí haría que el
    # filtro afirmara algo falso. Se quedan en GLOBAL, igual que Canadá
    # (Norteamérica sin ser EE.UU.) y Sudáfrica.
    "Japan": MarketRegion.ASIA,
    "China": MarketRegion.ASIA,
    "Hong Kong": MarketRegion.ASIA,
    "Taiwan": MarketRegion.ASIA,
    "South Korea": MarketRegion.ASIA,
    "India": MarketRegion.ASIA,
    "Singapore": MarketRegion.ASIA,
    "Indonesia": MarketRegion.ASIA,
    "Thailand": MarketRegion.ASIA,
    "Malaysia": MarketRegion.ASIA,
    "Philippines": MarketRegion.ASIA,
    "Vietnam": MarketRegion.ASIA,
    "United States": MarketRegion.US,
}

# ETFs cuya geografía ES el producto. Sin esta tabla no hay forma de saberla:
# el proveedor no da país para un ETF y el ticker no lo dice.
REGION_BY_SYMBOL: dict[str, MarketRegion] = {
    # Colombia
    "GXG": MarketRegion.COL,
    # Latinoamérica
    "EWZ": MarketRegion.LATAM,
    "EWW": MarketRegion.LATAM,
    "ECH": MarketRegion.LATAM,
    "EPU": MarketRegion.LATAM,
    "ARGT": MarketRegion.LATAM,
    "ILF": MarketRegion.LATAM,
    # Europa
    "VGK": MarketRegion.EU,
    "EZU": MarketRegion.EU,
    "FEZ": MarketRegion.EU,
    "IEV": MarketRegion.EU,
    "EWG": MarketRegion.EU,
    "EWQ": MarketRegion.EU,
    "EWU": MarketRegion.EU,
    "EWL": MarketRegion.EU,
    "EWN": MarketRegion.EU,
    "EWI": MarketRegion.EU,
    "EWP": MarketRegion.EU,
    "EWD": MarketRegion.EU,
    "EWK": MarketRegion.EU,
    "EWO": MarketRegion.EU,
    "EPOL": MarketRegion.EU,
    # Asia
    "EWJ": MarketRegion.ASIA,
    "DXJ": MarketRegion.ASIA,
    "EWY": MarketRegion.ASIA,
    "EWT": MarketRegion.ASIA,
    "EWH": MarketRegion.ASIA,
    "EWS": MarketRegion.ASIA,
    "EWM": MarketRegion.ASIA,
    "INDA": MarketRegion.ASIA,
    "INDY": MarketRegion.ASIA,
    "MCHI": MarketRegion.ASIA,
    "FXI": MarketRegion.ASIA,
    "KWEB": MarketRegion.ASIA,
    "ASHR": MarketRegion.ASIA,
    "EIDO": MarketRegion.ASIA,
    "THD": MarketRegion.ASIA,
}

# ETFs de renta fija internacional: no son "mercado estadounidense" aunque
# coticen allí. Sin esta lista caerían en US por la regla de tipo de abajo.
_INTERNATIONAL_BONDS = frozenset(
    {"BNDX", "BWX", "IGOV", "EMB", "VWOB", "PCY"}
)

# Sufijos de bolsa que identifican el mercado local por sí solos, cuando el
# proveedor todavía no ha rellenado el país (activo recién creado).
_SUFFIX_REGION: dict[str, MarketRegion] = {
    ".CL": MarketRegion.COL,
    ".SA": MarketRegion.LATAM,
    ".MX": MarketRegion.LATAM,
    ".SN": MarketRegion.LATAM,
    ".BA": MarketRegion.LATAM,
}

_EU_SUFFIXES = (
    ".DE", ".PA", ".L", ".AS", ".MC", ".MI", ".SW", ".CO",
    ".ST", ".OL", ".HE", ".BR", ".LS", ".VI",
)

_ASIA_SUFFIXES = (".T", ".HK", ".KS", ".TW", ".NS", ".BO", ".SI", ".SS", ".SZ")


def classify(asset: Asset) -> MarketRegion:
    """Región de un activo. Nunca devuelve None: GLOBAL es el cajón honesto.

    El orden importa. La declaración explícita gana al país, y el país gana al
    sufijo del ticker: el sufijo solo dice dónde se negocia, que es justo lo
    que NO queremos que decida.
    """
    symbol = asset.symbol.upper()

    declared = REGION_BY_SYMBOL.get(symbol)
    if declared is not None:
        return declared

    # Cripto y futuros no pertenecen a ninguna economía nacional.
    if asset.asset_type in (AssetType.CRYPTO, AssetType.OTHER):
        return MarketRegion.GLOBAL
    if symbol.endswith("=F") or symbol.endswith("-USD"):
        return MarketRegion.GLOBAL

    if asset.country:
        region = _COUNTRY_REGION.get(asset.country.strip())
        if region is not None:
            return region
        # País conocido por el proveedor pero fuera del mapa: Australia,
        # Canadá, Sudáfrica... No se inventa un cubo para cada uno; caen en
        # GLOBAL, que es donde el filtro deja de afirmar nada sobre ellos.
        return MarketRegion.GLOBAL

    for suffix, region in _SUFFIX_REGION.items():
        if symbol.endswith(suffix):
            return region
    if symbol.endswith(_EU_SUFFIXES):
        return MarketRegion.EU
    if symbol.endswith(_ASIA_SUFFIXES):
        return MarketRegion.ASIA
    if (asset.exchange or "").upper() == "BVC":
        return MarketRegion.COL

    if asset.asset_type in (AssetType.ETF, AssetType.FUND):
        if symbol in _INTERNATIONAL_BONDS:
            return MarketRegion.GLOBAL
        # Se reutiliza el cubo de exposición en vez de repetir aquí las listas
        # de ETFs: si divergieran, el filtro de mercado y el factor de
        # diversificación estarían clasificando el mismo activo de dos formas.
        from app.services import exposure

        bucket = exposure.exposure_bucket(asset)
        if bucket in (
            exposure.COMMODITIES,
            exposure.BROAD_EQUITY,
            # Un ETF de bitcoin al contado (IBIT, FBTC) cotiza en Nueva York y
            # no sigue a la economía estadounidense. Antes caía en
            # «Diversificado» por suposición y acababa aquí por accidente;
            # ahora llega por su clase y el resultado es el mismo a propósito.
            exposure.CRYPTO,
            exposure.DERIVATIVES,
        ):
            # Oro y "mundo entero" no son el mercado estadounidense aunque
            # coticen en él.
            return MarketRegion.GLOBAL
        # Sectorial (XLE) o renta fija del Tesoro (TLT): mercado de EE.UU.
        return MarketRegion.US

    return MarketRegion.GLOBAL


def parse_regions(raw: str | None) -> set[MarketRegion] | None:
    """`"US,COL"` -> {US, COL}. Devuelve None si no se pidió filtrar.

    Un valor desconocido se ignora en silencio en lugar de romper la petición:
    el filtro es una comodidad de la vista, y un 422 por un parámetro de UI
    dejaría la pantalla en blanco por algo que no impide responder.
    """
    if not raw or not raw.strip():
        return None
    valid = {r.value for r in MarketRegion}
    wanted = {
        token.strip().upper()
        for token in raw.split(",")
        if token.strip().upper() in valid
    }
    return {MarketRegion(v) for v in wanted} or None
