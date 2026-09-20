"""La ficha de compra de extremo a extremo, con el universo sintético."""

from __future__ import annotations

import pytest

from app.models import Asset
from tests.test_api import UNIVERSE, buy


def seed(client, db_of, portfolio_id):
    """Crea los activos con `?symbols` y los marca como universo, que es lo que
    hace el catálogo real: la ficha puntúa contra el universo ingestado."""
    client.get(f"/api/opportunities?portfolio_id={portfolio_id}&symbols={UNIVERSE}")
    db_of.query(Asset).update({"is_universe": True})
    db_of.commit()


def ficha(client, db_of, portfolio_id, symbol="SOLID"):
    seed(client, db_of, portfolio_id)
    return client.get(f"/api/opportunities/{symbol}/ficha?portfolio_id={portfolio_id}")


def test_ficha_has_all_sections(client, db_of, portfolio_id):
    response = ficha(client, db_of, portfolio_id)
    assert response.status_code == 200
    body = response.json()

    assert body["symbol"] == "SOLID"
    assert body["opportunity"]["symbol"] == "SOLID"
    assert body["verdict"]["level"] in {"red", "yellow", "green"}
    assert body["flags"], "siempre hay al menos la comprobación del ticker"
    assert any(f["code"] == "verify_ticker" for f in body["flags"])
    assert body["portfolio"]["position_count"] == 0
    assert "NO es una recomendación" in body["disclaimer"]
    assert "NO es una recomendación" in body["verdict"]["disclaimer"]


def test_ficha_verdict_counts_match_flags(client, db_of, portfolio_id):
    body = ficha(client, db_of, portfolio_id).json()
    for level in ("red", "yellow", "green", "info"):
        assert body["verdict"][level] == sum(1 for f in body["flags"] if f["level"] == level)


def test_ficha_unknown_symbol_is_404(client, portfolio_id):
    response = client.get(f"/api/opportunities/NOEXISTE/ficha?portfolio_id={portfolio_id}")
    assert response.status_code == 404


def test_ficha_unknown_portfolio_is_404(client):
    assert client.get("/api/opportunities/SOLID/ficha?portfolio_id=999").status_code == 404


def test_ficha_reflects_the_position_you_hold(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    assert buy(client, portfolio_id, "SOLID", qty="10", price="100").status_code == 201

    body = client.get(f"/api/opportunities/SOLID/ficha?portfolio_id={portfolio_id}").json()
    assert body["portfolio"]["position_count"] == 1
    assert body["portfolio"]["holding"] is not None
    assert float(body["portfolio"]["holding"]["quantity"]) == 10
    assert body["portfolio"]["holding"]["weight_pct"] == 100.0


def test_ficha_declares_data_age(client, db_of, portfolio_id):
    body = ficha(client, db_of, portfolio_id).json()
    fresh = body["freshness"]
    assert fresh["fundamentals_as_of"] is not None
    assert fresh["last_bar_date"] is not None


def test_ficha_peers_are_same_sector_and_exclude_itself(client, db_of, portfolio_id):
    # PRICEY, EXPENSIVE y RISKY comparten sector (Technology) en el universo.
    body = ficha(client, db_of, portfolio_id, "PRICEY").json()
    peer_symbols = {p["symbol"] for p in body["peers"]}
    assert "PRICEY" not in peer_symbols
    assert peer_symbols <= {"EXPENSIVE", "RISKY"}
    assert body["sector_size"] == 3
    assert 1 <= body["sector_rank"] <= 3


def test_ficha_without_fundamentals_raw_still_answers(client, db_of, portfolio_id):
    """Sin `raw` no hay salud financiera, pero la ficha no se cae ni inventa."""
    body = ficha(client, db_of, portfolio_id).json()
    health = body["health"]
    if not health["has_data"]:
        assert health["net_debt_to_ebitda"] is None
        assert health["caveats"]


def test_ficha_for_an_asset_without_data_is_red_and_explains_why(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    db_of.add(Asset(symbol="GHOST", name="Sin datos", currency="USD", is_universe=True))
    db_of.commit()

    body = client.get(f"/api/opportunities/GHOST/ficha?portfolio_id={portfolio_id}").json()
    assert body["opportunity"] is None
    assert body["excluded_reason"]
    assert body["verdict"]["level"] == "red"
    assert [f["code"] for f in body["flags"]] == ["not_ranked"]
    assert body["peers"] == []


def test_markdown_report_carries_the_disclaimer_and_flags(client, db_of, portfolio_id):
    from app.schemas.ficha import FichaResponse
    from app.services.ficha_markdown import render_markdown

    body = ficha(client, db_of, portfolio_id).json()
    text = render_markdown(FichaResponse.model_validate(body))

    assert text.startswith("# Ficha de compra: SOLID")
    assert "## Banderas" in text
    assert "NO es una recomendación" in text
    assert "Comprueba el instrumento con tu broker" in text


def test_ficha_includes_sizing_with_a_declared_stress_source(client, db_of, portfolio_id):
    body = ficha(client, db_of, portfolio_id).json()
    sizing = body["sizing"]
    assert sizing["stress_source"] in {"observed", "floor"}
    assert 0 < sizing["target_pct"] <= sizing["max_position_pct"]
    assert sizing["loss_if_repeats_pct_of_capital"] <= sizing["risk_budget_pct"] + 1e-9
    assert sizing["notes"]


def test_sizing_endpoint_uses_the_capital_you_give(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    url = f"/api/opportunities/SOLID/sizing?portfolio_id={portfolio_id}"

    base = client.get(f"{url}&capital=10000000&risk_budget_pct=2&max_position_pct=10").json()
    assert base["capital"] is not None
    assert float(base["target_amount"]) == pytest.approx(
        float(base["capital"]) * base["target_pct"] / 100, rel=1e-6
    )
    # Más presupuesto de riesgo, peso igual o mayor (nunca menor).
    bigger = client.get(f"{url}&capital=10000000&risk_budget_pct=4&max_position_pct=50").json()
    assert bigger["target_pct"] >= base["target_pct"]


def test_sizing_rejects_nonsense_limits(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    url = f"/api/opportunities/SOLID/sizing?portfolio_id={portfolio_id}"
    assert client.get(f"{url}&risk_budget_pct=0").status_code == 422
    assert client.get(f"{url}&capital=-5").status_code == 422
    assert client.get(f"{url}&max_position_pct=500").status_code == 422


def test_sizing_counts_what_you_already_hold(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    assert buy(client, portfolio_id, "SOLID", qty="10", price="100", fx="1").status_code == 201
    body = client.get(
        f"/api/opportunities/SOLID/sizing?portfolio_id={portfolio_id}&capital=100000"
    ).json()
    assert body["current_pct"] > 0
    assert body["add_pct"] == pytest.approx(max(0.0, body["target_pct"] - body["current_pct"]))
