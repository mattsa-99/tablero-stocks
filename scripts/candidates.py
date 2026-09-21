"""Candidatos a catálogo, agrupados por mercado y clase de activo.

Es una lista CURADA a propósito, no el volcado de un screener.

Se probó generar el catálogo automáticamente con `yf.screen` por región y
ningún filtro resultó fiable para la única pregunta que importa aquí -"¿es una
empresa realmente de este país o una cotización cruzada de otra?"-:

  - Las 25 mayores "alemanas" que devuelve el screener son TODAS ADRs de
    empresas estadounidenses (NVD.DE es NVIDIA, APC.DE es Apple). Lo mismo en
    México (NVDA.MX) y Brasil (NVDC34.SA, los DRN).
  - El campo `market` solo repite la región consultada: no distingue nada.
  - Filtrar por `currency != financialCurrency` sí caza esos casos, pero
    excluye a HSBC, Shell, AstraZeneca y BHP -británicas de verdad que
    reportan en USD- y a SQM, chilena que reporta en USD.

Meter esas cotizaciones cruzadas sería peor que no ampliar nada: el usuario
creería estar diversificando geográficamente mientras compra NVIDIA otra vez,
con ruido de tipo de cambio encima.

Los símbolos de aquí son CANDIDATOS: `build_catalog.py` los verifica uno a uno
contra Yahoo y descarta los que no existan. Ninguno llega al catálogo sin que
Yahoo haya devuelto precio y divisa para él.
"""

from __future__ import annotations

# ----------------------------------------------------------------------
# Estados Unidos
# ----------------------------------------------------------------------

US_STOCKS = """
AAPL MSFT NVDA AMZN GOOGL GOOG META AVGO TSLA BRK-B LLY JPM V UNH XOM MA
COST PG JNJ HD ABBV NFLX BAC CRM CVX MRK AMD KO PEP TMO WMT LIN ADBE ACN
MCD CSCO ABT PM ORCL IBM GE NOW QCOM TXN CAT DIS INTU VZ CMCSA AMGN DHR
PFE UNP SPGI NEE RTX LOW HON AXP BKNG SYK ELV BLK T PGR ETN TJX C VRTX
BSX SCHW ADP MDT MU LMT GILD CB PLD ADI MMC AMT SBUX DE UPS BA MDLZ ISRG
REGN SO ANET CI ZTS PANW KLAC LRCX SNPS CDNS MELI ICE PYPL WM EQIX APH
MO CME CL DUK ITW SHW MCK MSI PH NOC GD FDX TGT CSX EOG SLB PSX MPC VLO
OXY PSA SPG O CCI DLR WELL AVB EQR VTR ORLY AZO ROST LULU NKE F GM RIVN
COIN HOOD SQ SHOP UBER ABNB DASH SNOW DDOG CRWD ZS NET MDB TEAM WDAY
"""

US_ETFS = """
SPY VOO IVV VTI QQQ DIA IWM IWB IWD IWF VTV VUG VB VO MDY RSP SCHD VYM
DVY NOBL SPLV USMV QUAL MTUM VLUE SIZE COWZ
XLE XLF XLV XLK XLI XLY XLP XLU XLB XLRE XLC
VGT VHT VFH VDE VIS VCR VDC VPU VAW VNQ SMH SOXX IGV XBI IBB ITA XAR
KRE XOP OIH JETS TAN ICLN LIT REMX COPX
"""

# ----------------------------------------------------------------------
# Europa y Asia: primarias en su bolsa y su divisa. SOLO CATÁLOGO.
# La exposición a estas empresas entra por ADR_STOCKS, en USD.
# ----------------------------------------------------------------------

EUROPE_STOCKS = """
SAP.DE SIE.DE ALV.DE DTE.DE MUV2.DE MBG.DE BMW.DE VOW3.DE BAS.DE BAYN.DE
DBK.DE DB1.DE ADS.DE RWE.DE EOAN.DE IFX.DE HEN3.DE MRK.DE FRE.DE HEI.DE
MC.PA OR.PA TTE.PA SAN.PA AIR.PA SU.PA AI.PA EL.PA BNP.PA CS.PA RMS.PA
KER.PA SGO.PA DG.PA VIE.PA ORA.PA ML.PA STLAP.PA CAP.PA ACA.PA
SHEL.L AZN.L HSBA.L ULVR.L BP.L GSK.L RIO.L BATS.L DGE.L REL.L LSEG.L
NG.L BARC.L LLOY.L VOD.L TSCO.L PRU.L AAL.L GLEN.L IMB.L CPG.L
NESN.SW ROG.SW NOVN.SW UBSG.SW ZURN.SW ABBN.SW CFR.SW LONN.SW SIKA.SW
ASML.AS INGA.AS AD.AS PHIA.AS HEIA.AS WKL.AS DSFIR.AS
SAN.MC IBE.MC ITX.MC BBVA.MC TEF.MC REP.MC AMS.MC FER.MC
ISP.MI ENI.MI ENEL.MI UCG.MI STLAM.MI RACE.MI G.MI
NOVO-B.CO ORSTED.CO DSV.CO NZYM-B.CO
VOLV-B.ST ATCO-A.ST INVE-B.ST HM-B.ST ERIC-B.ST SEB-A.ST ASSA-B.ST SAND.ST
EQNR.OL DNB.OL TEL.OL NHY.OL
NOKIA.HE SAMPO.HE
"""

# ----------------------------------------------------------------------
# Asia-Pacífico
# ----------------------------------------------------------------------

ASIA_STOCKS = """
7203.T 6758.T 8306.T 9984.T 6501.T 8035.T 6098.T 6857.T 9432.T 4063.T
7974.T 6902.T 8058.T 8031.T 9433.T 4502.T 6981.T 7267.T 6273.T 6367.T
0700.HK 0939.HK 1299.HK 0005.HK 0388.HK 0941.HK 1810.HK 3690.HK 9988.HK
0016.HK 0002.HK 1398.HK 2318.HK 0883.HK 9618.HK
005930.KS 000660.KS 005380.KS 051910.KS 035420.KS 207940.KS
2330.TW 2317.TW 2454.TW 2308.TW 2882.TW
RELIANCE.NS TCS.NS HDFCBANK.NS INFY.NS ICICIBANK.NS HINDUNILVR.NS
BHARTIARTL.NS ITC.NS SBIN.NS LT.NS
BHP.AX CBA.AX CSL.AX NAB.AX WBC.AX ANZ.AX WES.AX MQG.AX RIO.AX WOW.AX
D05.SI O39.SI U11.SI Z74.SI
"""

# ----------------------------------------------------------------------
# Latinoamérica, con Colombia en su propia bolsa
# ----------------------------------------------------------------------

# Colombia: la ÚNICA bolsa extranjera que entra al universo, porque cotiza en
# COP y es donde el usuario compra directamente sin conversión.
BVC_STOCKS = """
ECOPETROL.CL GRUPOSURA.CL NUTRESA.CL ISA.CL CELSIA.CL GRUPOARGOS.CL
CEMARGOS.CL PFAVAL.CL GRUPOAVAL.CL ETB.CL PFDAVVNDA.CL CORFICOLCF.CL
TERPEL.CL PROMIGAS.CL CNEC.CL MINEROS.CL BVC.CL PFCEMARGOS.CL
PFGRUPSURA.CL PFGRUPOARG.CL BOGOTA.CL CONCONCRET.CL
"""

# Fondos cotizados de la BVC. En COP y por tanto sin conversión, que es la
# misma razón por la que entran las acciones colombianas.
#
# `GXTESCOL.CL` es el que justifica esta lista: da exposición a TES -deuda
# pública colombiana- con una serie de precios diaria y sin carga manual. Es
# la única renta fija local del tablero que no hay que teclear a mano.
#
# `TEVAICOL.CL` queda FUERA: se comprobó el 21-09-2026 y solo tenía 5 barras
# desde el 15 de septiembre. Un activo recién listado no se puede puntuar -ni
# volatilidad ni caída máxima ni tendencia- y entraría al ranking como ruido.
BVC_FUNDS = """
ICOLCAP.CL GXTESCOL.CL HCOLSEL.CL
"""

# Clase de activo DECLARADA, para lo que el proveedor no sabe clasificar.
#
# Yahoo no cubre la composición de los fondos de la BVC: llegan sin categoría
# y sin `funds_data`, así que el motor los supondría de acciones. Para
# ICOLCAP y HCOLSEL eso es correcto; para GXTESCOL es justo lo contrario de lo
# que es, y lo contaría como renta variable amplia en el reparto de la
# cartera. Se declara a mano porque es un hecho verificable, no una
# heurística, y por eso NO cuenta como supuesto aguas abajo.
DECLARED_CLASSES: dict[str, str] = {
    "GXTESCOL.CL": "renta_fija",
}

# ADRs y valores extranjeros que cotizan en USD en bolsas estadounidenses.
# ESTA es la vía a la diversificación global, no la bolsa local: se compran con
# los mismos dólares y su precio YA incorpora el movimiento de la divisa.
ADR_STOCKS = """
TM SONY MUFG SMFG HMC NTTYY SFTBY
TSM BABA JD PDD BIDU NTES TCEHY NIO LI XPEV
HDB IBN INFY WIT
SAP ASML NVO AZN GSK SHEL BP UL DEO RIO BHP HSBC BCS LYG
NVS RHHBY UBS ABBNY NSRGY SNY TTE MC BUD STLA RACE E
ENLAY IBDRY SAN BBVA PHG ING ERIC NOK VOD
SIEGY BASFY BAYRY ALIZY DTEGY MBGYY VWAGY ADDYY LVMUY PRNDY CFRUY EADSY
NPSNY DSDVY VLVLY EQNR
PBR VALE ITUB BBD ABEV CIG SBS GGB BSAC BCH ENIC LTM
AMX FMX TV KOF ASR PAC OMAB CX
EC CIB AVAL YPF GGAL BMA PAM TGS
"""

# Primarias extranjeras: NO entran al universo. Se quedan en el catálogo para
# que el buscador las encuentre y, si algún día se compran, la carga perezosa
# traiga sus datos. Mantenerlas cuesta bytes, no llamadas.
FOREIGN_PRIMARY = """
PETR4.SA VALE3.SA ITUB4.SA BBDC4.SA ABEV3.SA B3SA3.SA WEGE3.SA BBAS3.SA
RENT3.SA SUZB3.SA PRIO3.SA RADL3.SA LREN3.SA EQTL3.SA
WALMEX.MX GFNORTEO.MX FEMSAUBD.MX AMXB.MX GMEXICOB.MX CEMEXCPO.MX
TLEVISACPO.MX KIMBERA.MX BIMBOA.MX ASURB.MX GAPB.MX
CHILE.SN FALABELLA.SN SQM-B.SN COPEC.SN CENCOSUD.SN ENELCHILE.SN
CMPC.SN BSANTANDER.SN COLBUN.SN
"""

# ----------------------------------------------------------------------
# ETFs de país y región: la diversificación real con pocos símbolos.
# Cotizan en USD en EE.UU., así que no añaden un par de divisas nuevo.
# ----------------------------------------------------------------------

COUNTRY_ETFS = """
VEU VXUS EFA IEFA VEA IEV VGK EZU FEZ
EWG EWQ EWU EWL EWN EWI EWP EWD EWK EWO EWA EWC
EWJ DXJ EWY EWT EWH EWS EWM INDA INDY MCHI FXI KWEB ASHR EIDO THD
EEM VWO IEMG EMXC SCHE SPEM
EWZ EWW ECH EPU ILF ARGT GXG
EZA TUR EPOL EIS
VT ACWI URTH SPGM
"""

BOND_ETFS = """
AGG BND TLT IEF SHY IEI GOVT TIP VTIP SCHP STIP
LQD VCIT VCSH HYG JNK SJNK USHY
MUB VTEB EMB VWOB PCY BNDX BWX IGOV
SHV BIL SGOV MINT FLOT
"""

COMMODITY_ETFS = """
GLD IAU GLDM SGOL PHYS SLV SIVR PPLT PALL
DBC PDBC GSG DJP COMT USO BNO UNG DBA CORN WEAT SOYB
GDX GDXJ SIL RING
"""

CRYPTO = """
BTC-USD ETH-USD SOL-USD BNB-USD XRP-USD ADA-USD AVAX-USD DOT-USD
LINK-USD MATIC-USD LTC-USD BCH-USD DOGE-USD ATOM-USD UNI-USD XLM-USD
NEAR-USD ICP-USD FIL-USD ARB-USD OP-USD APT-USD
IBIT FBTC GBTC ETHA ARKB BITB
"""

FUTURES = """
GC=F SI=F CL=F BZ=F NG=F HG=F PL=F PA=F
ZC=F ZS=F ZW=F KC=F SB=F CC=F CT=F
ES=F NQ=F YM=F RTY=F ZB=F ZN=F DX=F
"""


# ----------------------------------------------------------------------
# Qué entra en el UNIVERSO DE INGESTA y qué se queda solo en el catálogo
# ----------------------------------------------------------------------
#
# EL CRITERIO ES LA DIVISA EN QUE SE FINANCIA, no el país de la empresa.
#
# Por el comisionista se compra en COP o en USD. Un valor cotizado en yenes o
# en euros no es comprable, así que rankearlo solo produce recomendaciones
# sobre las que no se puede actuar. Pero hay una razón más fuerte que la
# comodidad, y es de CORRECCIÓN:
#
#   `momentum_12_1`, la volatilidad y el drawdown se calculan sobre la serie
#   en DIVISA LOCAL, sin convertir (no existe histórico de tipos de cambio que
#   permitiera convertirla). Para quien financia en dólares eso mide otra cosa.
#   Medido sobre 2 años: Toyota en Tokio da +4,0% de momentum y su ADR en Nueva
#   York -4,7%. CAMBIA DE SIGNO. ASML: 130,9% frente a 114,8%.
#
# Restringir el universo a lo que se financia hace que ese error desaparezca
# por construcción, en lugar de parchearlo: el precio de un ADR YA incorpora el
# movimiento de la divisa, y un valor de la BVC en COP no necesita conversión.
#
# La diversificación global NO se pierde: entra por ETFs de país (EWJ, EWZ,
# MCHI) y por ADRs (TM, TSM, SAP, NSRGY), todos en USD.
FUNDING_CURRENCIES: frozenset[str] = frozenset({"USD", "COP"})

# Grupos candidatos al universo. La divisa manda por encima de esto: un símbolo
# de un grupo de aquí que resulte cotizar en otra divisa se queda fuera igual.
UNIVERSE_GROUPS: frozenset[str] = frozenset(
    {
        "us_stocks",
        "us_etfs",
        "adr_stocks",
        "bvc_stocks",
        "bvc_funds",
        "country_etfs",
        "bond_etfs",
        "commodity_etfs",
        "crypto",
        "futures",
    }
)


# Símbolos que se quedan en el CATÁLOGO pero nunca entran al universo, porque
# duplican a otro que sí está: la misma empresa bajo dos listados.
#
# Importa porque la guarda de "ya lo tienes" del motor de sugerencia compara
# SÍMBOLOS, no empresas (`suggestion.py`, `held_symbols`). Con Ecopetrol en la
# BVC y su ADR ambos en el universo, tener una y que te recomienden la otra
# sería doblar la apuesta en la misma empresa presentada como diversificación
# -exactamente el error que ese motor existe para evitar-.
#
# Criterio: para una empresa colombiana se conserva su listado en la BVC (en
# COP, sin conversión ni comisiones de ADR) y, entre ordinaria y preferencial,
# la preferencial, que es la que de hecho se negocia.
UNIVERSE_EXCLUSIONS: dict[str, str] = {
    "EC": "ADR de Ecopetrol; en el universo va ECOPETROL.CL, en COP",
    "AVAL": "ADR de Grupo Aval; en el universo va PFAVAL.CL",
    "GRUPOAVAL.CL": "ordinaria de Grupo Aval; se conserva la preferencial PFAVAL.CL",
    "CEMARGOS.CL": "ordinaria de Cementos Argos; se conserva PFCEMARGOS.CL",
    "GRUPOARGOS.CL": "ordinaria de Grupo Argos; se conserva PFGRUPOARG.CL",
    "GRUPOSURA.CL": "ordinaria de Grupo Sura; se conserva PFGRUPSURA.CL",
    "GOOG": "clase C de Alphabet, sin voto; se conserva GOOGL",
}


def in_universe(group: str, currency: str | None, symbol: str = "") -> bool:
    """Pertenencia al universo: grupo elegible Y divisa financiable.

    Se comprueba con la divisa VERIFICADA contra Yahoo, no con la que yo haya
    supuesto al escribir la lista. Así una clasificación equivocada -meter por
    error un valor de Londres entre los ADR- no cuela un activo incomprable en
    el universo: la divisa lo delata.

    `UNIVERSE_EXCLUSIONS` retira además los listados que duplican a otro ya
    presente. Siguen en el catálogo: buscables, no rankeados.
    """
    if symbol in UNIVERSE_EXCLUSIONS:
        return False
    return group in UNIVERSE_GROUPS and (currency or "") in FUNDING_CURRENCIES


def _symbols(block: str) -> list[str]:
    return [s for s in block.split() if s]


# (grupo, tipo por defecto, símbolos). El tipo es una expectativa: si Yahoo
# devuelve `quoteType` se usa el suyo, que es el dato de verdad.
GROUPS: list[tuple[str, str, list[str]]] = [
    ("us_stocks", "STOCK", _symbols(US_STOCKS)),
    ("us_etfs", "ETF", _symbols(US_ETFS)),
    ("adr_stocks", "STOCK", _symbols(ADR_STOCKS)),
    ("bvc_stocks", "STOCK", _symbols(BVC_STOCKS)),
    ("bvc_funds", "ETF", _symbols(BVC_FUNDS)),
    ("europe_stocks", "STOCK", _symbols(EUROPE_STOCKS)),
    ("asia_stocks", "STOCK", _symbols(ASIA_STOCKS)),
    ("foreign_primary", "STOCK", _symbols(FOREIGN_PRIMARY)),
    ("country_etfs", "ETF", _symbols(COUNTRY_ETFS)),
    ("bond_etfs", "ETF", _symbols(BOND_ETFS)),
    ("commodity_etfs", "ETF", _symbols(COMMODITY_ETFS)),
    ("crypto", "CRYPTO", _symbols(CRYPTO)),
    ("futures", "OTHER", _symbols(FUTURES)),
]


def all_candidates() -> list[tuple[str, str, str]]:
    """(símbolo, grupo, tipo esperado) sin duplicados, conservando el orden."""
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for group, kind, symbols in GROUPS:
        for symbol in symbols:
            if symbol in seen:
                continue
            seen.add(symbol)
            out.append((symbol, group, kind))
    return out


def normalized_company(name: str | None) -> str:
    """Nombre de empresa reducido a su núcleo, para detectar listados duplicados.

    "Ecopetrol S.A." y "Ecopetrol S.A." bajo dos símbolos son la misma empresa;
    la forma jurídica y la puntuación solo estorban al compararlas.
    """
    import re

    if not name:
        return ""
    text = name.lower()
    for suffix in (
        " s.a.b. de c.v.", " s.a.", " s.a", " plc", " inc.", " inc",
        " corporation", " corp.", " corp", " ltd.", " ltd", " limited",
        " company", " co.", " e.s.p.", " group", " holdings", " holding",
        " nv", " ag", " se", " & co. kgaa", ".",
    ):
        text = text.replace(suffix, "")
    return re.sub(r"[^a-z0-9]", "", text).strip()


def declared_class(symbol: str) -> str | None:
    """Clase declarada a mano para este símbolo, o None."""
    return DECLARED_CLASSES.get(symbol.upper())
