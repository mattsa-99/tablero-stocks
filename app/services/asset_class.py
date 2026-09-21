"""Clase de activo: a qué se parece esto, derivado de DATOS y no de listas.

POR QUÉ EXISTE
==============
El motor trataba igual a una acción, a un fondo de bonos, a una cesta de
materias primas y a un futuro del S&P. La consecuencia no era teórica:

  - SJNK, un fondo de bonos basura, salía con la MEJOR valoración del universo
    (99,9 sobre 100) porque Yahoo le asigna `trailingPE = 0,89`. Un fondo de
    bonos no tiene beneficios: ese número no significa nada.
  - TLT salía con 97,5 por un `priceToBook` de 0,54 calculado contra un
    `bookValue` de 148,87 que el propio Yahoo contradice en la misma respuesta
    (`navPrice` = 80,92).
  - PHYS, que solo tiene oro, salía con 98,8 porque Yahoo le imputa un
    «beneficio por acción» de 5,51 que es la revalorización del metal. Cuanto
    más sube el oro, más barato parece.

Y 136 de 490 activos (27,8%) acababan clasificados como «Diversificado» por
una heurística —«un ETF sin sector probablemente sea amplio»— que metía en el
mismo cubo a un fondo de deuda pública y a un índice mundial de acciones.

DE DÓNDE SALE LA CLASE
======================
De `Asset.fund_category`, la categoría Morningstar que el proveedor devuelve en
la misma respuesta que el resto de los metadatos. Cobertura medida sobre la
base real: **182 de 182 fondos**. No cuesta ni una llamada extra.

Se compara por NOMBRE EXACTO contra la tabla, nunca por subcadena. Al
desarrollar esto, una comparación por subcadena clasificó a XLC
(«Communications») como no accionario porque «com·muni·cations» contiene
«muni», de «Muni National Interm». Un fallo así no lanza nada: solo deja a un
ETF sectorial sin valoración para siempre.

EL SUFIJO `=F` MANDA SOBRE TODO
===============================
Es la única marca fiable de un futuro. Medido en la base: de los 21 símbolos
`=F` del universo, 20 traen `quoteType = FUTURE` y uno (`CT=F`) trae
`ALTSYMBOL`; y de esos 20, **`GC=F` y `CL=F` están guardados como `STOCK`**.
Ni `asset_type` ni `quoteType` los identifican; el sufijo sí.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.models import Asset, AssetType


class AssetClass(StrEnum):
    ACCION = "accion"
    FONDO_ACCIONES = "fondo_acciones"
    RENTA_FIJA = "renta_fija"
    MATERIAS_PRIMAS = "materias_primas"
    CRIPTO = "cripto"
    DERIVADO = "derivado"
    DESCONOCIDO = "desconocido"


CLASS_LABEL: dict[AssetClass, str] = {
    AssetClass.ACCION: "Acciones",
    AssetClass.FONDO_ACCIONES: "Fondos de acciones",
    AssetClass.RENTA_FIJA: "Renta fija",
    AssetClass.MATERIAS_PRIMAS: "Materias primas",
    AssetClass.CRIPTO: "Cripto",
    AssetClass.DERIVADO: "Derivados",
    AssetClass.DESCONOCIDO: "Sin clasificar",
}

# Clases que tienen BENEFICIOS y PATRIMONIO CONTABLE detrás, y por tanto
# admiten múltiplos de valoración. Es la frontera que `data_quality` usa.
EARNINGS_BACKED = frozenset({AssetClass.ACCION, AssetClass.FONDO_ACCIONES})


# ---------------------------------------------------------------------------
# Categorías Morningstar -> clase. Nombre EXACTO, nunca subcadena.
# ---------------------------------------------------------------------------

_FIXED_INCOME_CATEGORIES = (
    "Corporate Bond",
    "Emerging Markets Bond",
    "Global Bond",
    "Global Bond-USD Hedged",
    "High Yield Bond",
    "Inflation-Protected Bond",
    "Intermediate Core Bond",
    "Intermediate Government",
    "Long Government",
    "Muni National Interm",
    "Short Government",
    "Short-Term Bond",
    "Short-Term Inflation-Protected Bond",
    "Ultrashort Bond",
)

# Cestas de materia prima FÍSICA o por futuros: no hay empresas dentro.
# «Equity Precious Metals» y «Natural Resources» NO están aquí a propósito:
# son fondos de mineras y petroleras, es decir ACCIONES, y su P/E es real.
_COMMODITY_CATEGORIES = ("Commodities Broad Basket", "Commodities Focused")

_CRYPTO_CATEGORIES = ("Digital Assets",)

# Fondos de acciones AMPLIOS: diversificación en sí mismos.
_BROAD_EQUITY_CATEGORIES = (
    "Diversified Emerging Mkts", "Europe Stock", "Focused Region",
    "Foreign Large Blend", "Global Large-Stock Blend", "Greater China Region",
    "India Equity", "Japan Stock", "Large Blend", "Large Growth", "Large Value",
    "Mid-Cap Blend", "Mid-Cap Value", "Small Blend",
)

# Fondos de acciones CONCENTRADOS cuyo sector no se deduce de la categoría.
# ICLN y TAN (energía limpia) reparten entre utilities, tecnología e
# industriales sin que ninguna domine: colocarlos en un sector sería inventar,
# y llamarlos «amplios» también. Se tratan como amplios pero DECLARADO.
_UNPLACEABLE_EQUITY_CATEGORIES = ("Miscellaneous Sector",)

# Categoría -> sector GICS, para los fondos que se comportan como una acción de
# ese sector. Sustituye a la lista de símbolos que había antes: con esto, un
# ETF sectorial nuevo se clasifica solo.
#
# «Miscellaneous Sector» queda fuera a propósito: ver arriba.
CATEGORY_SECTOR: dict[str, str] = {
    "Communications": "Communication Services",
    "Consumer Cyclical": "Consumer Cyclical",
    "Consumer Defensive": "Consumer Defensive",
    "Equity Energy": "Energy",
    "Equity Precious Metals": "Basic Materials",
    "Financial": "Financial Services",
    "Health": "Healthcare",
    "Industrials": "Industrials",
    "Natural Resources": "Basic Materials",
    "Real Estate": "Real Estate",
    "Technology": "Technology",
    "Utilities": "Utilities",
}


CATEGORY_CLASS: dict[str, AssetClass] = {
    **{c: AssetClass.RENTA_FIJA for c in _FIXED_INCOME_CATEGORIES},
    **{c: AssetClass.MATERIAS_PRIMAS for c in _COMMODITY_CATEGORIES},
    **{c: AssetClass.CRIPTO for c in _CRYPTO_CATEGORIES},
    **{
        c: AssetClass.FONDO_ACCIONES
        for c in (
            *_BROAD_EQUITY_CATEGORIES,
            *_UNPLACEABLE_EQUITY_CATEGORIES,
            *CATEGORY_SECTOR,
        )
    },
}

_BY_VALUE: dict[str, AssetClass] = {c.value: c for c in AssetClass}

# Cuánto tiene que pesar una clase dentro del fondo para decidir por sí sola.
# Dos tercios es holgado a propósito: un fondo mixto 50/50 no es ninguna de
# las dos cosas, y forzarlo sería inventar. Esos caen al supuesto declarado.
_DOMINANT_SHARE = 0.66


def _class_from_composition(profile) -> AssetClass | None:
    """Clase según QUÉ TIENE DENTRO, o None si nada domina.

    `cashPosition` no decide: un fondo de materias primas por futuros aparece
    con el 100% en caja -GSG- porque la garantía está en letras del Tesoro, y
    llamarlo «renta fija» sería exactamente al revés de lo que es. Por eso el
    efectivo solo se reparte entre lo demás cuando lo demás ya decidió.
    """
    acciones = profile.stock_position or 0.0
    bonos = profile.bond_position or 0.0
    otros = profile.other_position or 0.0

    if bonos >= _DOMINANT_SHARE:
        return AssetClass.RENTA_FIJA
    if acciones >= _DOMINANT_SHARE:
        return AssetClass.FONDO_ACCIONES
    if otros >= _DOMINANT_SHARE:
        # Oro físico, bitcoin al contado, cestas de futuros. No se distingue
        # cuál sin la categoría, así que aquí NO se decide: la categoría ya
        # tuvo su turno antes y si llegó hasta aquí es que no la hay.
        return None
    return None


def _pct(value: float | None) -> str:
    return "sin dato" if value is None else f"{value * 100:.0f}%"


_FUND_TYPES = frozenset({AssetType.ETF, AssetType.FUND})
_COMPANY_TYPES = frozenset({AssetType.STOCK, AssetType.ADR, AssetType.REIT})


@dataclass(frozen=True)
class Classification:
    """La clase y, sobre todo, SI ES UN SUPUESTO.

    Un supuesto presentado como hecho es peor que un dato ausente: por eso la
    clase nunca viaja sola.
    """

    asset_class: AssetClass
    is_assumed: bool
    reason: str

    @property
    def label(self) -> str:
        return CLASS_LABEL[self.asset_class]

    @property
    def is_earnings_backed(self) -> bool:
        """True si tiene sentido preguntarle por un P/E o un P/B.

        UNA CLASE SUPUESTA NO BASTA PARA AFIRMAR QUE HAY BENEFICIOS.
        =============================================================
        Cuando el proveedor no categoriza un fondo se le supone de acciones,
        porque es lo más probable y porque hay que colocarlo en algún sitio
        para rankearlo. Pero «lo más probable» no es una base para conservar
        un múltiplo de valoración, que es justo el dato que este sistema
        descarta en cuanto no está respaldado.

        El caso vivo: Yahoo no categoriza los fondos de la BVC. `GXTESCOL.CL`
        es un ETF de TES -deuda pública colombiana- y sin esta regla se le
        supondría de acciones y conservaría un «P/E» que no significa nada.
        Es el mismo fallo de SJNK por otra puerta: la categoría lo tapó para
        los 182 fondos de hoy, no para los que se añadan mañana.

        Se sigue rankeando entre fondos de acciones -no desaparece- pero sin
        señal de valoración, que es la verdad.
        """
        return self.asset_class in EARNINGS_BACKED and not self.is_assumed


def classify(asset: Asset) -> Classification:
    """Clase de un activo. El orden de las comprobaciones es el argumento."""
    symbol = (asset.symbol or "").upper()

    # 1. El sufijo manda: ni `asset_type` ni `quoteType` cazan a GC=F ni a CT=F.
    if symbol.endswith("=F"):
        return Classification(
            AssetClass.DERIVADO, False, "El sufijo «=F» es un futuro continuo"
        )

    if asset.asset_type is AssetType.CRYPTO:
        return Classification(AssetClass.CRIPTO, False, "El proveedor lo marca como cripto")

    # 2. La clase DECLARADA en el catálogo, para lo que el proveedor no sabe.
    #    Va antes que la categoría porque es un dato verificado por una
    #    persona y la categoría puede faltar: `GXTESCOL.CL` es un ETF de deuda
    #    pública colombiana y Yahoo no lo categoriza ni le da perfil.
    declarada = (asset.declared_asset_class or "").strip().lower()
    if declarada in _BY_VALUE:
        return Classification(
            _BY_VALUE[declarada], False, "Clase declarada en el catálogo"
        )

    # 3. La categoría del proveedor, por nombre exacto.
    category = (asset.fund_category or "").strip()
    if category in CATEGORY_CLASS:
        return Classification(
            CATEGORY_CLASS[category], False, f"Categoría del proveedor: «{category}»"
        )

    # 4. QUÉ HAY DENTRO, que es el dato duro y no depende de que el proveedor
    #    haya categorizado nada. Un fondo con el 98,7% en bonos es de renta
    #    fija diga lo que diga su nombre.
    perfil = asset.fund_profile
    if perfil is not None:
        por_composicion = _class_from_composition(perfil)
        if por_composicion is not None:
            return Classification(
                por_composicion,
                False,
                "Composición declarada por el proveedor "
                f"(acciones {_pct(perfil.stock_position)}, "
                f"bonos {_pct(perfil.bond_position)}, "
                f"otros {_pct(perfil.other_position)})",
            )

    # 5. Un fondo sin categoría ni composición. Se sigue suponiendo que es de
    #    acciones -es lo más probable-, pero DECLARÁNDOLO, que es la diferencia
    #    con lo que había antes. Y sin derecho a múltiplos: ver
    #    `Classification.is_earnings_backed`.
    if asset.asset_type in _FUND_TYPES:
        detalle = f"categoría «{category}» no reconocida" if category else "sin categoría"
        return Classification(
            AssetClass.FONDO_ACCIONES, True,
            f"Es un fondo y se supone de acciones ({detalle})",
        )

    # 4. Una empresa es una empresa cuando el proveedor le reconoce un negocio.
    #    Sin sector NI industria no lo es: medido sobre el universo, los únicos
    #    tres casos son GC=F, CL=F y PHYS (un fideicomiso que solo tiene oro y
    #    al que Yahoo imputa un «beneficio por acción» que es la subida del
    #    metal). Tratarlo como acción es lo que le daba una valoración de 98,8.
    if asset.asset_type in _COMPANY_TYPES:
        if asset.sector or asset.industry:
            return Classification(
                AssetClass.ACCION, False, "Empresa con sector declarado por el proveedor"
            )
        return Classification(
            AssetClass.DESCONOCIDO, True,
            "Cotiza como acción pero el proveedor no le reconoce sector ni "
            "industria: no se puede afirmar que sea una empresa",
        )

    return Classification(
        AssetClass.DESCONOCIDO, True, f"Tipo «{asset.asset_type.value}» sin regla propia"
    )


def sector_of_fund(asset: Asset) -> str | None:
    """Sector GICS de un fondo sectorial, o None si es amplio o no lo es."""
    return CATEGORY_SECTOR.get((asset.fund_category or "").strip())


def bucket_is_assumed(asset: Asset) -> bool:
    """True si el CUBO de exposición sale de una suposición y no de un dato.

    Dos casos: la clase misma es supuesta (PHYS), o es un fondo de acciones
    cuya categoría no permite colocarlo en un sector ni afirmar que sea amplio
    («Miscellaneous Sector»).
    """
    clasificacion = classify(asset)
    if clasificacion.is_assumed:
        return True
    if clasificacion.asset_class is not AssetClass.FONDO_ACCIONES:
        return False
    category = (asset.fund_category or "").strip()
    return category not in CATEGORY_SECTOR and category not in _BROAD_EQUITY_CATEGORIES


# Barras por año de cada clase. Es lo que gobierna las ventanas y la
# anualización en `services/metrics.py`.
#
# El cripto no cierra: 365 barras al año frente a las ~252 de una bolsa. Dar
# por buenas las 252 subestimaba su volatilidad un 20,5% y convertía su
# «momentum a 12 meses» en uno de 7,6. Ver el docstring de `metrics`.
PERIODS_PER_YEAR: dict[AssetClass, int] = {AssetClass.CRIPTO: 365}
DEFAULT_PERIODS_PER_YEAR = 252


def periods_per_year(asset: Asset) -> int:
    """Barras por año que cabe esperar de la serie de este activo."""
    return PERIODS_PER_YEAR.get(
        classify(asset).asset_class, DEFAULT_PERIODS_PER_YEAR
    )


CLASS_SLUG: dict[AssetClass, str] = {c: c.value for c in AssetClass}
SLUG_TO_CLASS: dict[str, AssetClass] = {c.value: c for c in AssetClass}


def parse_asset_classes(raw: str | None) -> set[AssetClass] | None:
    """`"accion,cripto"` -> {ACCION, CRIPTO}. None si no se pidió filtrar.

    Un slug desconocido se ignora en vez de devolver 422, igual que en los
    otros filtros de vista: fallar dejaría la pantalla en blanco por un
    parámetro de interfaz y no por un problema con los datos.
    """
    if not raw or not raw.strip():
        return None
    wanted = {
        SLUG_TO_CLASS[token]
        for token in (t.strip().lower() for t in raw.split(","))
        if token in SLUG_TO_CLASS
    }
    return wanted or None
