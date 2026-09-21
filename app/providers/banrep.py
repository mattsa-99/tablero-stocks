"""Cliente de las estadísticas del Banco de la República (SUAMECA).

QUÉ APORTA Y POR QUÉ NO LO DA YAHOO
===================================
Yahoo no cubre nada colombiano que no cotice en bolsa. Estas son las tasas
contra las que se juzga un CDT o un TES, y sin ellas un 12% anual no se puede
leer: puede ser excelente o estar por debajo de la inflación.

Comprobado el 20-09-2026, sin autenticación y en JSON:

    curva cero cupón TES (COP y UVR, 1/5/10 años)  diaria, desde 2003
    IBR overnight, 1m, 3m, 6m, 12m                 diaria, desde 2008
    DTF 90 días y CDT 180/360                      semanal, desde 1984
    tasa de política monetaria                     diaria, desde 1998
    inflación total anual y meta                   mensual/anual
    UVR                                            diaria, desde 2000

LO QUE EL PROPIO BANREP ADVIERTE, y que hay que repetir aguas arriba: «Las
tasas TES son datos de tipo informativos y su fin no es la valoración de
portafolios». Sirven como REFERENCIA para decidir, no para valorar una
posición. Por eso `services/fixed_income.py` valora a costo más devengo y no
descontando con esta curva.

LA TRAMPA DEL CERTIFICADO
=========================
El servidor NO envía el certificado intermedio que une su hoja con la raíz:

    0  CN=suameca.banrep.gov.co        emitido por GeoTrust EV RSA CA G2
    1  DigiCert Global Root G2         <- salta directamente a la raíz

`curl` en macOS lo resuelve solo porque busca el intermedio por AIA; Python
con `certifi` NO, y falla con CERTIFICATE_VERIFY_FAILED. La solución NO es
`verify=False` -eso desactivaría la comprobación entera- sino añadir el
intermedio que falta, que es público y va versionado en `certs/`. La
verificación sigue activa.
"""

from __future__ import annotations

import datetime as dt
import logging
import ssl
from dataclasses import dataclass
from pathlib import Path

import certifi
import httpx

from app.core.exceptions import ProviderUnavailable, ProviderUnreachable

logger = logging.getLogger(__name__)

BASE = (
    "https://suameca.banrep.gov.co/estadisticas-economicas-back/rest/"
    "estadisticaEconomicaRestService"
)
REFERER = "https://suameca.banrep.gov.co/estadisticas-economicas/"

_CERT_DIR = Path(__file__).parent / "certs"
_INTERMEDIATE = _CERT_DIR / "banrep-intermediate.pem"


@dataclass(frozen=True)
class RateSeries:
    """Una serie de referencia: cómo se pide y cómo se llama aquí."""

    key: str
    menu_id: int
    series_id: int
    label: str
    unit: str


# Las series que el tablero usa. El `menu_id` agrupa varias en una sola
# llamada; el `series_id` es lo que identifica a cada una dentro de la
# respuesta y es estable (no depende del orden).
SERIES: tuple[RateSeries, ...] = (
    RateSeries("tes_cop_1y", 220002, 15272, "TES en pesos a 1 año", "%"),
    RateSeries("tes_cop_5y", 220002, 15273, "TES en pesos a 5 años", "%"),
    RateSeries("tes_cop_10y", 220002, 15274, "TES en pesos a 10 años", "%"),
    RateSeries("tes_uvr_1y", 220002, 15275, "TES en UVR a 1 año (tasa real)", "%"),
    RateSeries("tes_uvr_5y", 220002, 15276, "TES en UVR a 5 años (tasa real)", "%"),
    RateSeries("tes_uvr_10y", 220002, 15277, "TES en UVR a 10 años (tasa real)", "%"),
    RateSeries("dtf_90d", 220003, 65, "DTF a 90 días", "%"),
    RateSeries("cdt_180d", 220003, 67, "CDT a 180 días (mercado)", "%"),
    RateSeries("cdt_360d", 220003, 68, "CDT a 360 días (mercado)", "%"),
    RateSeries("ibr_on", 241, 241, "IBR overnight", "%"),
    RateSeries("ibr_1m", 241, 242, "IBR a 1 mes", "%"),
    RateSeries("ibr_3m", 241, 243, "IBR a 3 meses", "%"),
    RateSeries("ibr_6m", 241, 16560, "IBR a 6 meses", "%"),
    RateSeries("ibr_12m", 241, 16562, "IBR a 12 meses", "%"),
    RateSeries("politica_monetaria", 59, 59, "Tasa de política del Banrep", "%"),
    RateSeries("inflacion_anual", 100001, 15270, "Inflación total anual", "%"),
    RateSeries("meta_inflacion", 100001, 853, "Meta de inflación", "%"),
    RateSeries("uvr", 100005, 850, "Unidad de Valor Real (UVR)", "COP"),
)

BY_KEY: dict[str, RateSeries] = {s.key: s for s in SERIES}


def _ssl_context() -> ssl.SSLContext:
    """Contexto con el intermedio que el servidor omite. Verificación ACTIVA."""
    context = ssl.create_default_context(cafile=certifi.where())
    if _INTERMEDIATE.exists():
        context.load_verify_locations(cafile=str(_INTERMEDIATE))
    return context


class BanrepClient:
    """Solo lectura sobre el servicio público. Ninguna clave, ningún estado."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def _get(self, path: str, params: dict) -> object:
        try:
            with httpx.Client(
                timeout=self.timeout, verify=_ssl_context(), follow_redirects=True
            ) as client:
                response = client.get(
                    f"{BASE}/{path}", params=params, headers={"Referer": REFERER}
                )
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderUnavailable(
                f"Banrep devolvió HTTP {exc.response.status_code} en {path}"
            ) from exc
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderUnreachable(f"No se pudo conectar con Banrep: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Fallo consultando Banrep: {exc}") from exc

    def fetch_series(self, keys: list[str]) -> dict[str, list[tuple[dt.date, float]]]:
        """Series pedidas, agrupadas por menú para no repetir llamadas.

        Devuelve solo lo que llegó: una serie ausente es una serie ausente, no
        una lista vacía que aguas abajo pareciera «sin datos hoy».
        """
        pedidas = [BY_KEY[k] for k in keys if k in BY_KEY]
        if not pedidas:
            return {}

        por_menu: dict[int, list[RateSeries]] = {}
        for serie in pedidas:
            por_menu.setdefault(serie.menu_id, []).append(serie)

        resultado: dict[str, list[tuple[dt.date, float]]] = {}
        for menu_id, series in sorted(por_menu.items()):
            payload = self._get("consultaMenuXId", {"idMenu": menu_id})
            if not isinstance(payload, dict):
                continue
            crudas = {
                s.get("id"): s
                for s in (payload.get("SERIES") or [])
                if isinstance(s, dict)
            }
            for serie in series:
                bruta = crudas.get(serie.series_id)
                if bruta is None:
                    logger.warning(
                        "Banrep: la serie %s (id %s) no vino en el menú %s",
                        serie.key, serie.series_id, menu_id,
                    )
                    continue
                puntos = _parse_points(bruta.get("data") or [])
                if puntos:
                    resultado[serie.key] = puntos
        return resultado


def _parse_points(data: list) -> list[tuple[dt.date, float]]:
    """`[[epoch_ms, valor], ...]` -> [(fecha, valor)] ascendente.

    Las marcas son medianoche de Bogotá (UTC-5) en milisegundos, así que el
    día correcto sale del FLOOR de la división por un día: convertirlas a UTC
    las correría al día anterior en las horas de la madrugada.
    """
    puntos: list[tuple[dt.date, float]] = []
    for fila in data:
        if not isinstance(fila, list | tuple) or len(fila) < 2:
            continue
        marca, valor = fila[0], fila[1]
        if valor is None:
            continue
        try:
            dias = int(marca) // 86_400_000
            puntos.append((dt.date(1970, 1, 1) + dt.timedelta(days=dias), float(valor)))
        except (TypeError, ValueError):
            continue
    puntos.sort()
    return puntos
