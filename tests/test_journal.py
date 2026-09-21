"""Diario de decisiones y lista de vigilancia."""

from __future__ import annotations

import datetime as dt

import pytest

from app.models import Asset, JournalEntry, Transaction
from tests.test_api import UNIVERSE, buy

THESIS = "Negocio estable con caja libre y deuda baja; la compro para 5 años."


def seed(client, db_of, portfolio_id):
    client.get(f"/api/opportunities?portfolio_id={portfolio_id}&symbols={UNIVERSE}")
    db_of.query(Asset).update({"is_universe": True})
    db_of.commit()


def create(client, portfolio_id, **overrides):
    body = {"symbol": "SOLID", "kind": "WATCH", "thesis": THESIS}
    body.update(overrides)
    return client.post(f"/api/journal?portfolio_id={portfolio_id}", json=body)


def days(n):
    return (dt.date.today() + dt.timedelta(days=n)).isoformat()


# --------------------------------------------------------------------------
# Crear
# --------------------------------------------------------------------------


def test_create_takes_a_snapshot_and_defaults_review_to_90_days(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    response = create(client, portfolio_id)
    assert response.status_code == 201
    body = response.json()

    assert body["symbol"] == "SOLID"
    assert body["is_active"] is True
    assert body["days_to_review"] == 90
    assert body["snapshot"]["score"] is not None
    assert body["snapshot"]["rank"] is not None
    assert body["snapshot"]["grade"] in {"A", "B", "C", "D", "E"}
    assert body["snapshot"]["verdict"] in {"red", "yellow", "green"}
    assert any(f["code"] == "verify_ticker" for f in body["snapshot"]["flags"])
    assert body["snapshot"]["price"] is not None


def test_a_journal_entry_never_touches_the_ledger(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    before = db_of.query(Transaction).count()
    assert create(client, portfolio_id, kind="BUY").status_code == 201
    db_of.expire_all()
    assert db_of.query(Transaction).count() == before
    summary = client.get(f"/api/portfolios/{portfolio_id}").json()
    assert summary["positions"] == []


def test_a_one_word_thesis_is_rejected(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    assert create(client, portfolio_id, thesis="sube").status_code == 422
    assert create(client, portfolio_id, thesis="         ").status_code == 422


def test_review_date_must_be_in_the_future(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    assert create(client, portfolio_id, review_date=days(-1)).status_code == 422
    assert create(client, portfolio_id, review_date=days(0)).status_code == 422
    assert create(client, portfolio_id, review_date=days(1)).status_code == 201


def test_unknown_symbol_and_unknown_portfolio_are_404(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    assert create(client, portfolio_id, symbol="NOEXISTE").status_code == 404
    assert create(client, 999).status_code == 404


def test_invalid_price_and_unknown_fields_are_rejected(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    assert create(client, portfolio_id, invalidation_price="0").status_code == 422
    assert create(client, portfolio_id, invalidation_price="-3").status_code == 422
    assert create(client, portfolio_id, inventado="x").status_code == 422
    assert create(client, portfolio_id, kind="COMPRAR").status_code == 422


# --------------------------------------------------------------------------
# Alertas
# --------------------------------------------------------------------------


def test_price_below_invalidation_level_raises_a_red_alert(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    # El precio de SOLID ronda 100; un nivel de 1.000 está por encima => cruzado.
    entry = create(client, portfolio_id, invalidation_price="1000").json()
    assert any(a["code"] == "invalidation_breached" and a["level"] == "red"
               for a in entry["alerts"])

    listing = client.get(f"/api/journal?portfolio_id={portfolio_id}").json()
    assert listing["counts"]["breached"] == 1
    assert listing["entries"][0]["symbol"] == "SOLID"


def test_price_above_invalidation_level_does_not_alert(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    entry = create(client, portfolio_id, invalidation_price="0.01").json()
    assert not any(a["code"] == "invalidation_breached" for a in entry["alerts"])


def test_overdue_review_alerts_and_review_resets_it(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    entry = create(client, portfolio_id).json()

    # Se envejece la entrada a mano: la API no deja crear fechas pasadas.
    row = db_of.get(JournalEntry, entry["id"])
    row.review_date = dt.date.today() - dt.timedelta(days=3)
    db_of.commit()

    overdue = client.get(f"/api/journal?portfolio_id={portfolio_id}").json()
    assert overdue["counts"]["overdue"] == 1
    assert any(a["code"] == "review_overdue" for a in overdue["entries"][0]["alerts"])

    reviewed = client.post(
        f"/api/journal/{entry['id']}/review",
        json={"note": "Sigue igual: caja libre estable"},
    ).json()
    assert reviewed["review_note"] == "Sigue igual: caja libre estable"
    assert reviewed["reviewed_at"] is not None
    assert reviewed["days_to_review"] == 90
    assert not any(a["code"] == "review_overdue" for a in reviewed["alerts"])


def test_review_soon_is_only_informational(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    entry = create(client, portfolio_id, review_date=days(3)).json()
    soon = [a for a in entry["alerts"] if a["code"] == "review_soon"]
    assert soon and soon[0]["level"] == "info"
    counts = client.get(f"/api/journal/summary?portfolio_id={portfolio_id}").json()
    assert counts["with_alerts"] == 0


def test_grade_drop_alerts_only_from_two_steps(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    entry = create(client, portfolio_id).json()
    row = db_of.get(JournalEntry, entry["id"])

    now_grade = client.get(f"/api/journal?portfolio_id={portfolio_id}").json()["entries"][0][
        "now"
    ]["grade"]
    levels = {"A": 5, "B": 4, "C": 3, "D": 2, "E": 1}
    letters = {v: k for k, v in levels.items()}

    # Foto de hace 2 escalones por encima de hoy => alerta.
    row.snapshot_grade = letters[min(5, levels[now_grade] + 2)] if levels[now_grade] <= 3 else "A"
    db_of.commit()
    listing = client.get(f"/api/journal?portfolio_id={portfolio_id}").json()
    dropped = any(a["code"] == "grade_dropped" for a in listing["entries"][0]["alerts"])
    assert dropped == (levels[row.snapshot_grade] - levels[now_grade] >= 2)

    # Un solo escalón no alerta.
    row.snapshot_grade = letters[min(5, levels[now_grade] + 1)]
    db_of.commit()
    listing = client.get(f"/api/journal?portfolio_id={portfolio_id}").json()
    assert not any(a["code"] == "grade_dropped" for a in listing["entries"][0]["alerts"])


def test_archived_entries_do_not_alert_and_are_hidden_by_default(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    entry = create(client, portfolio_id, invalidation_price="1000").json()

    closed = client.post(
        f"/api/journal/{entry['id']}/review",
        json={"note": "Tesis cumplida, cierro", "archive": True},
    ).json()
    assert closed["is_active"] is False
    assert closed["alerts"] == []

    assert client.get(f"/api/journal?portfolio_id={portfolio_id}").json()["entries"] == []
    everything = client.get(f"/api/journal?portfolio_id={portfolio_id}&include_archived=true")
    assert len(everything.json()["entries"]) == 1


# --------------------------------------------------------------------------
# Editar, borrar, contexto
# --------------------------------------------------------------------------


def test_patch_changes_only_what_is_sent_and_null_clears(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    entry = create(client, portfolio_id, invalidation="Si recorta el dividendo",
                   invalidation_price="80").json()

    patched = client.patch(f"/api/journal/{entry['id']}", json={"kind": "BUY"}).json()
    assert patched["kind"] == "BUY"
    assert patched["thesis"] == THESIS
    assert patched["invalidation"] == "Si recorta el dividendo"
    assert float(patched["invalidation_price"]) == 80

    cleared = client.patch(
        f"/api/journal/{entry['id']}", json={"invalidation_price": None, "invalidation": None}
    ).json()
    assert cleared["invalidation_price"] is None
    assert cleared["invalidation"] is None

    assert client.patch(f"/api/journal/{entry['id']}", json={"thesis": "corta"}).status_code == 422
    assert client.patch(
        f"/api/journal/{entry['id']}", json={"review_date": days(-5)}
    ).status_code == 422
    assert client.patch("/api/journal/9999", json={"kind": "HOLD"}).status_code == 404


def test_delete_removes_the_entry(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    entry = create(client, portfolio_id).json()
    assert client.delete(f"/api/journal/{entry['id']}").status_code == 204
    assert client.delete(f"/api/journal/{entry['id']}").status_code == 404
    assert client.get(f"/api/journal?portfolio_id={portfolio_id}").json()["entries"] == []


def test_entry_shows_whether_you_hold_the_asset(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    create(client, portfolio_id)
    url = f"/api/journal?portfolio_id={portfolio_id}"
    assert client.get(url).json()["entries"][0]["held"] is False

    assert buy(client, portfolio_id, "SOLID", qty="1", price="100", fx="1").status_code == 201
    assert client.get(url).json()["entries"][0]["held"] is True


def test_entries_needing_attention_come_first(client, db_of, portfolio_id):
    seed(client, db_of, portfolio_id)
    calm = create(client, portfolio_id, symbol="FAIR").json()
    risky = create(client, portfolio_id, symbol="SOLID", invalidation_price="1000").json()
    symbols = [e["symbol"] for e in client.get(
        f"/api/journal?portfolio_id={portfolio_id}").json()["entries"]]
    assert symbols == [risky["symbol"], calm["symbol"]]


def test_watched_assets_join_the_ingestion_universe(client, db_of, portfolio_id):
    """Vigilar sin refrescar el precio sería una promesa vacía."""
    from app.services import universe as universe_service

    seed(client, db_of, portfolio_id)
    outsider = db_of.query(Asset).filter_by(symbol="RISKY").one()
    outsider.is_universe = False
    db_of.commit()
    assert "RISKY" not in {a.symbol for a in universe_service.get_ingestion_universe(db_of)}

    assert create(client, portfolio_id, symbol="RISKY").status_code == 201
    db_of.expire_all()
    assert "RISKY" in {a.symbol for a in universe_service.get_ingestion_universe(db_of)}

    # Al cerrarla, deja de refrescarse.
    entry_id = db_of.query(JournalEntry).one().id
    assert client.post(f"/api/journal/{entry_id}/review",
                       json={"note": "cerrada", "archive": True}).status_code == 200
    db_of.expire_all()
    assert "RISKY" not in {a.symbol for a in universe_service.get_ingestion_universe(db_of)}


@pytest.mark.parametrize("path", ["", "/summary"])
def test_journal_needs_a_valid_portfolio(client, path):
    assert client.get(f"/api/journal{path}?portfolio_id=12345").status_code == 404
