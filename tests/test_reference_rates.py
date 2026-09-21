"""Tasas de referencia colombianas: contra qué se juzga un CDT o un TES.

Ninguna prueba toca la red: un cliente falso devuelve series sintéticas, que
es lo que permite comprobar que un fallo del proveedor NO rompe nada.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.core.exceptions import ProviderUnreachable
from app.models import ReferenceRate
from app.providers.banrep import BY_KEY, SERIES, _parse_points
from app.services import reference_rates as rr


class ClienteFalso:
    def __init__(self, series=None, error=None):
        self.series = series or {}
        self.error = error
        self.llamadas = 0

    def fetch_series(self, keys):
        self.llamadas += 1
        if self.error:
            raise self.error
        return {k: v for k, v in self.series.items() if k in keys}


def serie(*valores) -> list[tuple[dt.date, float]]:
    hoy = dt.date.today()
    return [
        (hoy - dt.timedelta(days=len(valores) - 1 - i), v)
        for i, v in enumerate(valores)
    ]


def test_the_latest_value_carries_its_date(db):
    """La curva TES se publica con retraso y la inflación es mensual.

    Un «12,66%» sin fecha se lee como el dato de hoy.
    """
    rr.refresh(db, ClienteFalso({"tes_cop_10y": serie(12.5, 12.66)}))
    ultimas = rr.latest(db, ["tes_cop_10y"])

    lectura = ultimas["tes_cop_10y"]
    assert lectura.value == 12.66
    assert lectura.as_of == dt.date.today()
    assert lectura.label == "TES en pesos a 10 años"


def test_a_provider_failure_returns_a_warning_not_an_exception(db):
    """Banrep caído no puede tumbar nada: son datos de referencia, no del ledger."""
    escritas, avisos = rr.refresh(
        db, ClienteFalso(error=ProviderUnreachable("sin red"))
    )
    assert escritas == 0
    assert any("no disponibles" in a for a in avisos)


def test_a_missing_series_is_named(db):
    """Una serie ausente se declara en vez de quedar como si no se hubiera pedido."""
    _, avisos = rr.refresh(
        db, ClienteFalso({"ibr_on": serie(11.18)}), keys=["ibr_on", "tes_cop_1y"]
    )
    assert any("tes_cop_1y" in a for a in avisos)


def test_a_revised_value_overwrites_the_old_one(db):
    """Banrep revisa series hacia atrás: en 2021 recalculó tres años de curva.

    Con `insert_ignore_duplicates` la corrección quedaría fuera para siempre.
    """
    hoy = dt.date.today()
    rr.refresh(db, ClienteFalso({"tes_cop_1y": [(hoy, 12.20)]}))
    rr.refresh(db, ClienteFalso({"tes_cop_1y": [(hoy, 12.35)]}))

    filas = db.query(ReferenceRate).filter(ReferenceRate.series == "tes_cop_1y").all()
    assert len(filas) == 1, "Una fila por serie y fecha"
    assert filas[0].value == 12.35


def test_the_real_rate_uses_fisher_not_a_subtraction():
    """Con tasas de dos dígitos la resta se queda corta de forma apreciable.

    12,3% con 6,24% de inflación da 5,70% real, no 6,06%. Esos 36 puntos
    básicos son el 6% del rendimiento real, y en renta fija eso es mucho.
    """
    exacto = rr.real_rate(12.3, 6.24)
    resta = 12.3 - 6.24

    assert exacto == pytest.approx(5.7038, abs=0.001)
    assert resta - exacto == pytest.approx(0.356, abs=0.01)


def test_a_stale_monthly_series_is_flagged(db):
    """La inflación es mensual: más de 45 días ya no es el dato vigente."""
    viejo = dt.date.today() - dt.timedelta(days=60)
    rr.refresh(db, ClienteFalso({"inflacion_anual": [(viejo, 6.24)]}))

    lectura = rr.latest(db, ["inflacion_anual"])["inflacion_anual"]
    assert lectura.is_stale_monthly is True


def test_epoch_marks_are_read_as_bogota_days():
    """Las marcas son medianoche de Bogotá (UTC-5) en milisegundos.

    Convertirlas a UTC las correría al día anterior en las horas de madrugada,
    y una curva diaria desplazada un día es un error que no lanza nada.
    """
    # 2026-09-11 a medianoche de Bogotá = 2026-09-11T05:00Z
    marca = int(dt.datetime(2026, 9, 11, 5, 0, tzinfo=dt.UTC).timestamp() * 1000)
    puntos = _parse_points([[marca, 12.2]])

    assert puntos == [(dt.date(2026, 9, 11), 12.2)]


def test_every_series_has_a_label_and_a_unit():
    """Sin etiqueta, la ficha mostraría `tes_uvr_5y` en crudo."""
    for s in SERIES:
        assert s.label and s.unit
        assert BY_KEY[s.key] is s


def test_the_menus_are_grouped_into_one_call_each(db):
    """Seis series de TES viven en el mismo menú: pedirlas por separado serían
    seis llamadas para la misma respuesta."""
    menus = {s.menu_id for s in SERIES}
    assert len(menus) < len(SERIES)


# ----------------------------------------------------------------------
# Tasas de CDT por banco
# ----------------------------------------------------------------------


class ClienteCdtFalso:
    def __init__(self, ofertas=None, error=None):
        self.ofertas = ofertas or []
        self.error = error

    def fetch_offers(self, **kwargs):
        if self.error:
            raise self.error
        return self.ofertas


def oferta(entidad, plazo, tasa, dias_atras=1, monto=1_000_000.0):
    from app.providers.datos_gov import CdtOffer

    return CdtOffer(
        as_of=dt.date.today() - dt.timedelta(days=dias_atras),
        entity=entidad, term_label=plazo, rate_pct=tasa, amount=monto,
    )


def test_the_term_is_matched_to_the_published_buckets(db):
    """La fuente no trae días: el nombre del plazo ES la clave.

    Pedir 365 días recoge «A 360 DIAS» y «SUPERIORES A 360 DIAS», que es lo
    comparable. Fuera de los rangos publicados no hay respuesta, y decirlo es
    mejor que estirar el plazo más cercano.
    """
    rr.refresh_cdt_offers(db, ClienteCdtFalso([
        oferta("Bancolombia", "A 360 DIAS", 11.81),
        oferta("Coltefinanciera", "SUPERIORES A 360 DIAS", 13.56),
        oferta("Otro", "A 30 DIAS", 9.74),
    ]))

    # 365 días NO es «a 360 dias», que es exactamente 360: cae en el tramo
    # de arriba. La fuente publica tramos con nombre, no días.
    anual = rr.compare_cdt(db, days=365)
    assert anual.term_labels == ("SUPERIORES A 360 DIAS",)
    assert anual.best_rate_pct == 13.56

    exacto = rr.compare_cdt(db, days=360, issuer="Bancolombia")
    assert exacto.term_labels == ("A 360 DIAS",)
    assert exacto.issuer_rate_pct == 11.81

    assert rr.compare_cdt(db, days=50_000) is None, "Fuera de rango no se estira"


def test_the_bucket_used_is_declared(db):
    """«Superiores a 360 días» mezcla un CDT a un año con uno a cinco.

    Sin decir qué tramo se usó no hay forma de saber si la comparación es
    fina o gruesa, y las dos se presentarían igual.
    """
    rr.refresh_cdt_offers(db, ClienteCdtFalso([
        oferta("A", "SUPERIORES A 360 DIAS", 13.0),
        oferta("B", "A 90 DIAS", 10.0),
    ]))

    grueso = rr.compare_cdt(db, days=1800)
    assert grueso.term_labels == ("SUPERIORES A 360 DIAS",)

    fino = rr.compare_cdt(db, days=90)
    assert fino.term_labels == ("A 90 DIAS",)


def test_both_the_best_and_your_own_bank_are_reported(db):
    """La mejor sin la tuya no dice si moverse; la tuya sin la mejor no dice
    cuánto estás dejando encima de la mesa."""
    rr.refresh_cdt_offers(db, ClienteCdtFalso([
        oferta("Coltefinanciera", "SUPERIORES A 360 DIAS", 13.56),
        oferta("Bancolombia", "SUPERIORES A 360 DIAS", 12.80),
        oferta("Banco Chico", "SUPERIORES A 360 DIAS", 13.00),
    ]))

    c = rr.compare_cdt(db, days=400, issuer="bancolombia")
    assert c.best_entity == "Coltefinanciera"
    assert c.issuer_name == "Bancolombia", "La búsqueda del emisor no distingue mayúsculas"
    assert c.median_rate_pct == 13.00
    assert "PACTADAS" in c.caveat, "No son las tasas de la vitrina, y se declara"


def test_an_unknown_issuer_still_gives_the_market(db):
    """Un CDT de una cooperativa que no publica no puede dejar sin respuesta
    a «¿qué paga el mercado?»."""
    rr.refresh_cdt_offers(db, ClienteCdtFalso([
        oferta("Coltefinanciera", "A 360 DIAS", 13.56),
    ]))

    c = rr.compare_cdt(db, days=360, issuer="Cooperativa Inexistente")
    assert c.best_rate_pct == 13.56
    assert c.issuer_rate_pct is None
    assert c.issuer_name is None


def test_a_provider_failure_is_a_warning_not_an_exception(db):
    escritas, avisos = rr.refresh_cdt_offers(
        db, ClienteCdtFalso(error=ProviderUnreachable("sin red"))
    )
    assert escritas == 0
    assert any("no disponibles" in a for a in avisos)


def test_rates_are_rounded_for_presentation(db):
    """La mediana de dos tasas sale con la cola de coma flotante."""
    rr.refresh_cdt_offers(db, ClienteCdtFalso([
        oferta("A", "A 360 DIAS", 11.81),
        oferta("B", "A 360 DIAS", 10.6),
    ]))
    c = rr.compare_cdt(db, days=360)
    assert c.median_rate_pct == 11.21, "11.204999999999998 sin redondear"


def test_channels_are_not_terms():
    """«CAPTACIONES A TRAVES DE CDT POR RED DE OFICINAS» es un canal.

    Mezclarlo con los plazos daría una media de cosas incomparables.
    """
    from app.providers.datos_gov import PLAZOS

    assert "CAPTACIONES A TRAVES DE CDT POR RED DE OFICINAS" not in PLAZOS
    assert "CAPTACIONES A TRAVES DE CDT POR TESORERIA" not in PLAZOS
    assert all(lo <= hi for lo, hi in PLAZOS.values())


# ----------------------------------------------------------------------
# No se le descuenta la inflación a lo que ya es real
# ----------------------------------------------------------------------


def test_a_uvr_rate_is_already_real_and_is_not_discounted_again(db):
    """El doble conteo que el propio CLAUDE.md prohíbe, por otra puerta.

    La UVR ajusta el CAPITAL con la inflación, así que la tasa pactada ya es
    lo que se gana POR ENCIMA de ella. Restándosela otra vez, los TES en UVR
    salían con 0,0%, −0,9% y −0,05% cuando son 6,24%, 5,28% y 6,19%.

    El fallo estaba a la vista en la respuesta del asesor y venía de decidir
    la naturaleza de la serie con un `if unit == "%"`.
    """
    rr.refresh(db, ClienteFalso({
        "inflacion_anual": serie(6.24),
        "tes_uvr_10y": serie(6.24),
        "tes_cop_10y": serie(12.66),
    }))
    lecturas = rr.latest(db)

    assert lecturas["tes_uvr_10y"].is_already_real is True
    assert lecturas["tes_uvr_10y"].is_nominal_rate is False
    assert lecturas["tes_cop_10y"].is_nominal_rate is True

    # Y la comprobación cruzada que valida las dos: el TES en pesos a 10 años
    # descontado da 6,04% y el de UVR -que es real de mercado- marca 6,24%.
    # Dos medidas independientes a 20 puntos básicos.
    calculada = rr.real_rate(lecturas["tes_cop_10y"].value, 6.24)
    assert calculada == pytest.approx(6.04, abs=0.01)
    assert abs(calculada - lecturas["tes_uvr_10y"].value) < 0.3


def test_an_inflation_target_is_not_a_rate_anybody_earns(db):
    """Su «tasa real» es la distancia entre dos medidas de inflación."""
    rr.refresh(db, ClienteFalso({
        "inflacion_anual": serie(6.24),
        "meta_inflacion": serie(3.0),
        "uvr": serie(419.6686),
    }))
    lecturas = rr.latest(db)

    assert lecturas["meta_inflacion"].is_nominal_rate is False
    assert lecturas["uvr"].is_nominal_rate is False


def test_every_series_declares_what_it_represents():
    """Sin el campo, el consumidor acaba decidiéndolo con `unit == '%'`."""
    from app.providers.banrep import SERIES

    validas = {"nominal", "real", "inflation", "target", "index"}
    for s in SERIES:
        assert s.nature in validas, f"{s.key}: naturaleza «{s.nature}» desconocida"
        if s.unit != "%":
            assert not s.is_nominal_rate, f"{s.key} no es una tasa"
    # Las de UVR son las únicas ya reales, y tienen que estar las tres.
    reales = {s.key for s in SERIES if s.nature == "real"}
    assert reales == {"tes_uvr_1y", "tes_uvr_5y", "tes_uvr_10y"}
