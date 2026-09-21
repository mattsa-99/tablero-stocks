"""Tasas de CDT efectivamente pactadas, por banco y por plazo.

QUÉ ES Y QUÉ NO ES
==================
Viene del conjunto `axk9-g2nh` de datos.gov.co, que publica la Superintendencia
Financiera: las emisiones de CDT de cada entidad vigilada, con su tasa y el
monto emitido. 2.069.978 filas desde 2018, con unos cuatro días de retraso.

NO es la tasa de la vitrina. Es lo que los bancos pactaron DE VERDAD, ponderado
por monto. Suele ser mejor dato que el anuncio -nadie publica el descuento que
hizo por un depósito grande- pero no es lo que te van a ofrecer a ti por
diez millones, y la interfaz tiene que decirlo.

POR QUÉ IMPORTA MÁS QUE LA TASA AGREGADA
========================================
Banrep publica un CDT a 360 días del mercado (12,31% el 25-09-2026). Sirve para
saber si una oferta va bien o mal encaminada, pero la decisión real es entre
bancos concretos: en el corte del 16-09-2026, Coltefinanciera pagó 13,56% a más
de 360 días y Bancolombia 13,02%. Medio punto sobre veinte millones a un año
son cien mil pesos, y la diferencia entre los dos NO es la tasa: es quién te
debe el dinero.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import httpx

from app.core.exceptions import ProviderUnavailable, ProviderUnreachable

logger = logging.getLogger(__name__)

BASE = "https://www.datos.gov.co/resource/axk9-g2nh.json"

# Cuántos días atrás se mira. Diez cubren de sobra el hueco del corte a medias
# y dejan fuera lo bastante viejo como para que una tasa haya cambiado.
WINDOW_DAYS = 10

# La unidad de captura que agrupa las emisiones de CDT. El conjunto mezcla
# ocho unidades distintas -saldos de ahorro, interbancarios, repos- y sin
# filtrar por esta saldrían tasas que no son de CDT.
UNIDAD_CDT = "EMISIONES PUNTUALES Y RANGOS DE EMISION DE CDT"

# Plazos tal y como los nombra la fuente, ordenados. La cadena es la clave: el
# conjunto no trae un campo numérico de días, así que agrupar exige el nombre.
PLAZOS: dict[str, tuple[int, int]] = {
    "A 30 DIAS": (1, 30),
    "ENTRE 61 Y 89 DIAS": (61, 89),
    "A 90 DIAS": (90, 90),
    "ENTRE 91 Y 119 DIAS": (91, 119),
    "A 120 DIAS": (120, 120),
    "ENTRE 121 Y 179 DIAS": (121, 179),
    "A 180 DIAS": (180, 180),
    "ENTRE 181 Y 359 DIAS": (181, 359),
    "A 360 DIAS": (360, 360),
    "SUPERIORES A 360 DIAS": (361, 10_000),
}


@dataclass(frozen=True)
class CdtOffer:
    """Una emisión de CDT: quién, a qué plazo, a qué tasa y por cuánto."""

    as_of: dt.date
    entity: str
    term_label: str
    rate_pct: float
    amount: float

    @property
    def term_days(self) -> tuple[int, int] | None:
        return PLAZOS.get(self.term_label)

    def covers(self, days: int) -> bool:
        rango = self.term_days
        return rango is not None and rango[0] <= days <= rango[1]


class DatosGovClient:
    """Solo lectura sobre la API pública. Sin clave; con ella sube la cuota."""

    def __init__(self, timeout: float = 30.0, app_token: str | None = None) -> None:
        self.timeout = timeout
        self.app_token = app_token

    def _get(self, params: dict) -> list[dict]:
        headers = {"X-App-Token": self.app_token} if self.app_token else {}
        try:
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                response = client.get(BASE, params=params, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderUnavailable(
                f"datos.gov.co devolvió HTTP {exc.response.status_code}"
            ) from exc
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderUnreachable(f"No se pudo conectar con datos.gov.co: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"Fallo consultando datos.gov.co: {exc}") from exc

    def fetch_offers(self, *, days: int = WINDOW_DAYS) -> list[CdtOffer]:
        """Última tasa de cada banco y plazo dentro de una ventana reciente.

        SE PIDE UNA VENTANA Y NO EL ÚLTIMO CORTE, y la razón está medida. Los
        cortes son diarios con ~400 emisiones cada uno, pero **el más reciente
        siempre está a medias** porque se publica por partes: el 18-09-2026
        tenía 15 filas cuando el 17 tenía 403 y el 16, 420. Quedándose con el
        último, Bancolombia no aparecía en absoluto y «la mejor tasa del
        mercado» salía de dos entidades pequeñas.

        Dentro de la ventana se conserva la emisión MÁS RECIENTE de cada
        (entidad, plazo): una tasa de hace una semana sigue siendo orientativa
        y no tenerla es peor que tenerla con su fecha a la vista.

        No se pide el histórico completo -dos millones de filas- porque no
        responde ninguna pregunta que el tablero haga.
        """
        desde = dt.date.today() - dt.timedelta(days=days)
        filas = self._get({
            "$where": (
                f"nombre_unidad_de_captura='{UNIDAD_CDT}' "
                f"AND fechacorte>'{desde.isoformat()}T00:00:00.000'"
            ),
            "$order": "fechacorte DESC",
            "$limit": "20000",
        })

        # (entidad, plazo) -> la emisión más reciente. Las filas vienen
        # ordenadas de nueva a vieja, así que la primera que llega gana.
        mejor: dict[tuple[str, str], CdtOffer] = {}
        for fila in filas:
            plazo = (fila.get("descripcion") or "").strip().upper()
            if plazo not in PLAZOS:
                # «CAPTACIONES A TRAVES DE CDT POR RED DE OFICINAS» y «POR
                # TESORERIA» son CANALES, no plazos: mezclarlos con los plazos
                # daría una media de cosas incomparables.
                continue
            try:
                tasa = float(fila.get("tasa") or 0)
                monto = float(fila.get("monto") or 0)
                corte = dt.date.fromisoformat((fila.get("fechacorte") or "")[:10])
            except (TypeError, ValueError):
                continue
            if tasa <= 0:
                continue

            entidad = (fila.get("nombreentidad") or "").strip().strip('"')
            clave = (entidad, plazo)
            if clave in mejor:
                continue
            mejor[clave] = CdtOffer(
                as_of=corte,
                entity=entidad,
                term_label=plazo,
                rate_pct=tasa,
                amount=monto,
            )
        return sorted(mejor.values(), key=lambda o: (-o.rate_pct, o.entity))
