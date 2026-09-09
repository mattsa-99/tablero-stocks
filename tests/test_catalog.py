"""Invariantes del catálogo semilla y de su relación con el universo.

El catálogo es datos, no código, y por eso mismo se rompe en silencio: una
divisa ausente o un símbolo del universo que no está en el catálogo no lanzan
ninguna excepción, solo producen una valoración equivocada.
"""

from __future__ import annotations

from app.models import AssetType
from app.services import catalog as catalog_service
from app.services import universe as universe_service


def test_every_catalog_entry_declares_a_currency():
    """Sin divisa, `seed_catalog` cae al USD por defecto y valora mal.

    Es el fallo que hacía inviable un catálogo internacional: un valor alemán
    sembrado como USD se convierte a la base con el tipo equivocado hasta el
    primer refresco de metadatos, con tiempo de sobra para registrar una compra
    cuyo `fx_rate_to_base` ya queda congelado.
    """
    missing = [e["symbol"] for e in catalog_service.load_catalog() if not e.get("currency")]
    assert not missing, f"Sin divisa declarada: {missing}"


def test_catalog_currencies_are_iso_codes_in_upper_case():
    """"GBp" nunca debe llegar al catálogo: se normaliza en el proveedor."""
    for entry in catalog_service.load_catalog():
        currency = entry["currency"]
        assert len(currency) == 3, f"{entry['symbol']}: divisa {currency!r}"
        assert currency.isupper(), (
            f"{entry['symbol']}: {currency!r} sin normalizar. 'GBp' son peniques "
            "y vale 100 veces menos que 'GBP'"
        )


def test_the_catalog_is_actually_international():
    """La razón de ser de la ampliación: si todo es USD, no diversifica nada."""
    currencies = {e["currency"] for e in catalog_service.load_catalog()}
    assert len(currencies) >= 8, f"Solo {len(currencies)} divisas: {currencies}"
    assert {"USD", "EUR", "JPY"} <= currencies


def test_asset_types_are_valid_domain_values():
    for entry in catalog_service.load_catalog():
        AssetType(entry["asset_type"])  # lanza si el valor no existe


def test_no_duplicate_symbols():
    symbols = [e["symbol"] for e in catalog_service.load_catalog()]
    assert len(symbols) == len(set(symbols))


# ----------------------------------------------------------------------
# Catálogo y universo no pueden desincronizarse
# ----------------------------------------------------------------------


def test_every_universe_symbol_exists_in_the_catalog():
    """El universo se declara DENTRO del catálogo, así que esto es estructural.

    Si un símbolo pudiera estar en el universo sin estar en el catálogo,
    `seed_universe` lo crearía con los valores por defecto -USD y STOCK-, que
    para un valor japonés o un ETF de bonos es sencillamente falso.
    """
    catalog_symbols = {e["symbol"] for e in catalog_service.load_catalog()}
    universe = set(catalog_service.universe_symbols_from_catalog())
    assert universe <= catalog_symbols
    assert universe, "El catálogo no declara ningún miembro del universo"


def test_default_symbols_come_from_the_catalog():
    from_catalog = catalog_service.universe_symbols_from_catalog()
    assert universe_service.default_symbols() == from_catalog


def test_the_universe_only_holds_what_can_actually_be_bought():
    """El universo se limita a las divisas en que se financia: USD y COP.

    No es una simplificación cosmética, son dos problemas a la vez:

    1. Rankear un valor cotizado en yenes produce recomendaciones sobre las que
       no se puede actuar, porque el comisionista solo compra en COP o USD.
    2. Y sobre todo, es de CORRECCIÓN. `momentum_12_1`, la volatilidad y el
       drawdown se calculan sobre la serie en divisa LOCAL, sin convertir -no
       hay histórico de tipos de cambio con el que convertirla-. Medido sobre
       dos años, Toyota da +4,0% de momentum en Tokio y -4,7% vía su ADR:
       CAMBIA DE SIGNO. Restringir el universo hace que ese error desaparezca
       por construcción: el precio de un ADR ya incorpora el movimiento de la
       divisa, y un valor de la BVC en COP no necesita conversión.
    """
    from scripts.candidates import FUNDING_CURRENCIES

    by_symbol = {e["symbol"]: e for e in catalog_service.load_catalog()}
    universe = catalog_service.universe_symbols_from_catalog()

    foreign = sorted(
        {
            f"{s} ({by_symbol[s]['currency']})"
            for s in universe
            if by_symbol[s]["currency"] not in FUNDING_CURRENCIES
        }
    )
    assert not foreign, f"En el universo pero no comprable: {foreign}"


def test_the_universe_covers_every_asset_class_requested():
    """Diversificación global SIN salir de USD/COP: ETFs de país y ADRs."""
    by_symbol = {e["symbol"]: e for e in catalog_service.load_catalog()}
    universe = catalog_service.universe_symbols_from_catalog()
    kinds = {by_symbol[s]["asset_type"] for s in universe}
    assert {"STOCK", "ETF", "CRYPTO"} <= kinds

    assert any(s.endswith(".CL") for s in universe), "Bolsa de Valores de Colombia"
    assert any(s.startswith("BTC") for s in universe), "Cripto"
    assert "GLD" in universe or "GC=F" in universe, "Oro"
    assert "TLT" in universe or "AGG" in universe, "Renta fija"
    # La exposición internacional entra por aquí, no por la bolsa local.
    assert {"EWJ", "EWZ", "MCHI"} <= set(universe), "ETFs de país"
    assert {"TM", "TSM", "NSRGY"} <= set(universe), "ADRs en USD"


def test_foreign_primaries_stay_searchable_without_being_ingested():
    """Toyota en Tokio sigue en el catálogo, pero no se rankea.

    Es justo para esto que existe la separación catálogo/universo: el buscador
    la encuentra, y si algún día se comprara, la carga perezosa traería sus
    datos. Cuesta bytes, no llamadas.
    """
    by_symbol = {e["symbol"]: e for e in catalog_service.load_catalog()}
    universe = set(catalog_service.universe_symbols_from_catalog())

    for symbol in ("7203.T", "SHEL.L", "SAP.DE"):
        assert symbol in by_symbol, f"{symbol} debe seguir siendo buscable"
        assert symbol not in universe, f"{symbol} no se financia en USD ni COP"


def test_the_catalog_is_strictly_larger_than_the_universe():
    """La distinción tiene que ser real, no una coincidencia de que todo entre."""
    total = len(catalog_service.load_catalog())
    universe = len(catalog_service.universe_symbols_from_catalog())
    assert universe < total, (
        "Si el catálogo y el universo coinciden, la separación que hace "
        "escalable el sistema está dormida"
    )


def test_colombian_assets_quote_in_cop():
    """La BVC cotiza en COP: para una cartera en COP no necesitan conversión.

    Además fija lo que el CLAUDE.md daba por pendiente -"los tickers de la BVC
    no están verificados"-: ahora salen del generador y solo entran si Yahoo
    devolvió precio y divisa para ellos.
    """
    bvc = [e for e in catalog_service.load_catalog() if e["symbol"].endswith(".CL")]
    assert bvc, "No hay activos de la BVC en el catálogo"
    assert all(e["currency"] == "COP" for e in bvc)


# ----------------------------------------------------------------------
# Sembrado
# ----------------------------------------------------------------------


def test_seeding_marks_declared_universe_members(db):
    from app.models import Asset

    catalog_service.seed_catalog(db)
    universe = set(catalog_service.universe_symbols_from_catalog())

    marked = {
        a.symbol for a in db.query(Asset).filter(Asset.is_universe.is_(True)).all()
    }
    assert marked == universe


def test_seeding_preserves_the_declared_currency(db):
    from app.models import Asset

    catalog_service.seed_catalog(db)
    declared = {e["symbol"]: e["currency"] for e in catalog_service.load_catalog()}
    for asset in db.query(Asset).all():
        assert asset.currency == declared[asset.symbol]


def test_seeding_is_idempotent(db):
    catalog_service.seed_catalog(db)
    second = catalog_service.seed_catalog(db)
    assert second["created"] == 0


def test_catalog_only_assets_are_not_ingested(db):
    """El catálogo cuesta bytes; el universo cuesta llamadas. No son lo mismo."""
    catalog_service.seed_catalog(db)
    total = catalog_service.catalog_size(db)
    ingested = len(universe_service.get_ingestion_universe(db))
    assert ingested <= total


def test_catalog_currencies_are_known_iso_codes():
    """Toda divisa del catálogo debe estar en la tabla de magnitudes.

    Es la red que caza las SUBUNIDADES que la caja no delata. "USX" -centavos
    de dólar, en los que cotizan el maíz y el café- tiene tres letras y va en
    mayúsculas, así que pasa por ISO y burla cualquier comprobación de forma.
    Solo se detecta porque no es una divisa que exista.

    Exigir la pertenencia a `CURRENCY_USD_MAGNITUDE` mata dos pájaros: obliga a
    que la divisa sea real, y garantiza que sus tipos de cambio tienen banda de
    sanidad. Una divisa desconocida entraría sin ninguna comprobación de
    inversión.
    """
    from app.services.market_data import CURRENCY_USD_MAGNITUDE

    unknown = sorted(
        {
            e["currency"]
            for e in catalog_service.load_catalog()
            if e["currency"] not in CURRENCY_USD_MAGNITUDE
        }
    )
    assert not unknown, (
        f"Divisas desconocidas en el catálogo: {unknown}. Si alguna es una "
        "subunidad (como USX o GBp), añádela a MINOR_UNIT_CURRENCIES; si es una "
        "divisa real nueva, añádela a CURRENCY_USD_MAGNITUDE."
    )


def test_no_company_appears_twice_in_the_universe():
    """Una empresa, un listado. Si no, el motor recomienda lo que ya tienes.

    La guarda de "ya lo tienes" de `suggestion.py` compara SÍMBOLOS, no
    empresas. Con Ecopetrol presente a la vez como ECOPETROL.CL (BVC) y EC
    (ADR), tener una y recibir la otra como sugerencia sería doblar la apuesta
    en la misma empresa presentada como diversificación -justo el error que ese
    motor existe para evitar-.

    El chequeo es por nombre normalizado y no por una lista de pares conocidos,
    para que un duplicado NUEVO se detecte solo al ampliar el catálogo.
    """
    from collections import defaultdict

    from scripts.candidates import normalized_company

    by_symbol = {e["symbol"]: e for e in catalog_service.load_catalog()}
    groups: dict[str, list[str]] = defaultdict(list)
    for symbol in catalog_service.universe_symbols_from_catalog():
        key = normalized_company(by_symbol[symbol].get("name"))
        if key:
            groups[key].append(symbol)

    duplicated = {k: v for k, v in groups.items() if len(v) > 1}
    assert not duplicated, (
        "Empresas con más de un listado en el universo: "
        + "; ".join(f"{k}: {v}" for k, v in sorted(duplicated.items()))
        + ". Añade el sobrante a candidates.UNIVERSE_EXCLUSIONS."
    )


def test_excluded_duplicates_are_still_searchable():
    """Retirar del universo no es borrar: el ADR se sigue pudiendo buscar."""
    from scripts.candidates import UNIVERSE_EXCLUSIONS

    catalog_symbols = {e["symbol"] for e in catalog_service.load_catalog()}
    universe = set(catalog_service.universe_symbols_from_catalog())
    for symbol in UNIVERSE_EXCLUSIONS:
        assert symbol in catalog_symbols, f"{symbol} debe seguir en el catálogo"
        assert symbol not in universe
