"""Importación de operaciones desde CSV: todo o nada, sin adivinar formatos."""

from __future__ import annotations

import pytest

from app.models import Asset, Transaction
from app.services.importer import COLUMNS, TEMPLATE

HEADER = ",".join(COLUMNS)


def csv_of(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def post(client, portfolio_id, text, *, dry_run=None):
    query = f"portfolio_id={portfolio_id}"
    if dry_run is not None:
        query += f"&dry_run={str(dry_run).lower()}"
    return client.post(f"/api/transactions/import?{query}", json={"csv": text})


def count(db_of):
    db_of.expire_all()
    return db_of.query(Transaction).count()


DEPOSIT = "2026-01-15,DEPOSIT,,,,5000000,0,COP,1,,"
BUY = "2026-01-20,BUY,AAPL,2,185.50,,1.00,USD,4150.25,,"
SELL = "2026-02-10,SELL,AAPL,1,200.10,,1.00,USD,4100.00,,"
DIVIDEND = "2026-03-15,DIVIDEND,KO,,,3.20,0.50,USD,4050.00,,"


def test_template_is_served_and_passes_its_own_dry_run(client, portfolio_id, db_of):
    served = client.get("/api/transactions/import/template")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("text/csv")
    assert served.text == TEMPLATE

    report = post(client, portfolio_id, TEMPLATE).json()
    assert report["errors"] == []
    assert report["dry_run"] is True and report["applied"] is False
    assert report["new_rows"] == 4
    assert report["by_type"] == {"DEPOSIT": 1, "BUY": 1, "SELL": 1, "DIVIDEND": 1}
    assert count(db_of) == 0


def test_dry_run_is_the_default_and_writes_nothing(client, portfolio_id, db_of):
    report = post(client, portfolio_id, csv_of(DEPOSIT, BUY)).json()
    assert report["applied"] is False
    assert count(db_of) == 0


def test_apply_imports_everything_and_positions_follow(client, portfolio_id, db_of):
    report = post(client, portfolio_id, csv_of(DEPOSIT, BUY, SELL, DIVIDEND), dry_run=False).json()
    assert report["applied"] is True
    assert report["new_rows"] == 4
    assert count(db_of) == 4

    summary = client.get(f"/api/portfolios/{portfolio_id}").json()
    held = {p["symbol"]: float(p["quantity"]) for p in summary["positions"]}
    assert held == {"AAPL": 1.0}
    assert {"AAPL", "KO"} <= {a.symbol for a in db_of.query(Asset).all()}


def test_importing_the_same_file_twice_does_not_duplicate(client, portfolio_id, db_of):
    text = csv_of(DEPOSIT, BUY, SELL)
    assert post(client, portfolio_id, text, dry_run=False).json()["applied"] is True
    again = post(client, portfolio_id, text, dry_run=False).json()

    assert again["applied"] is False
    assert again["new_rows"] == 0
    assert again["duplicate_rows"] == 3
    assert again["errors"] == []
    assert count(db_of) == 3


def test_identical_legit_rows_in_one_file_are_both_kept(client, portfolio_id, db_of):
    report = post(client, portfolio_id, csv_of(DEPOSIT, BUY, BUY), dry_run=False).json()
    assert report["new_rows"] == 3 and report["errors"] == []
    assert count(db_of) == 3
    # ...y reimportar sigue sin duplicar ninguna de las dos.
    again = post(client, portfolio_id, csv_of(DEPOSIT, BUY, BUY), dry_run=False).json()
    assert again["new_rows"] == 0


def test_one_bad_row_imports_nothing(client, portfolio_id, db_of):
    bad = "2026-02-01,BUY,AAPL,abc,10,,0,USD,4000,,"
    report = post(client, portfolio_id, csv_of(DEPOSIT, bad, BUY), dry_run=False).json()

    assert report["applied"] is False
    assert count(db_of) == 0
    assert [e["line"] for e in report["errors"]] == [3]
    assert "no es un número" in report["errors"][0]["message"]
    assert "todo o nada" in report["message"].lower()


def test_selling_what_you_do_not_have_points_at_the_row(client, portfolio_id, db_of):
    oversell = "2026-02-15,SELL,AAPL,5,200,,0,USD,4100,,"
    report = post(client, portfolio_id, csv_of(DEPOSIT, BUY, SELL, oversell)).json()

    assert report["new_rows"] == 4
    assert len(report["errors"]) == 1
    assert report["errors"][0]["line"] == 5
    assert "supera" in report["errors"][0]["message"]
    assert "hora" in report["errors"][0]["message"]  # sugiere fijar el orden
    assert count(db_of) == 0


def test_ledger_is_checked_against_what_already_exists(client, portfolio_id, db_of):
    assert post(client, portfolio_id, csv_of(DEPOSIT, BUY), dry_run=False).json()["applied"]
    # Vender 3 cuando solo se tienen 2.
    report = post(client, portfolio_id, csv_of("2026-02-01,SELL,AAPL,3,190,,0,USD,4100,,")).json()
    assert report["errors"] and "supera" in report["errors"][0]["message"]


@pytest.mark.parametrize(
    "row,fragment",
    [
        ("2026-02-01,BUY,AAPL,1,1.234,50,,0,USD,4000,,", "columnas"),  # más columnas
        ("2026-02-01,BUY,AAPL,1,\"10,5\",,0,USD,4000,,", "punto decimal"),
        ("01/02/2026,BUY,AAPL,1,10,,0,USD,4000,,", "no válida"),
        ("2026-02-01T10:00:00,BUY,AAPL,1,10,,0,USD,4000,,", "zona horaria"),
        ("2999-01-01,BUY,AAPL,1,10,,0,USD,4000,,", "futuro"),
        ("2026-02-01,COMPRAR,AAPL,1,10,,0,USD,4000,,", "no válido"),
        ("2026-02-01,BUY,AAPL,,10,,0,USD,4000,,", "quantity"),
        ("2026-02-01,BUY,AAPL,-1,10,,0,USD,4000,,", "quantity"),
        ("2026-02-01,DEPOSIT,,,,-5,0,COP,1,,", "cash_amount"),
        ("2026-02-01,BUY,ZZZ,1,10,,0,,4000,,", "currency"),  # símbolo nuevo sin divisa
        ("2026-02-01,BUY,AAPL,1,10,,0,USD,,,", "tipo de cambio"),  # sin FX y sin tabla
    ],
)
def test_bad_rows_are_rejected_with_a_line_number(client, portfolio_id, row, fragment):
    report = post(client, portfolio_id, csv_of(row)).json()
    assert report["errors"], row
    assert report["errors"][0]["line"] == 2
    assert fragment in report["errors"][0]["message"], report["errors"][0]["message"]
    assert report["new_rows"] == 0


def test_unknown_columns_are_refused_instead_of_guessed(client, portfolio_id):
    text = "Fecha,Operación,Ticker,Cantidad\n2026-01-01,Compra,AAPL,1\n"
    report = post(client, portfolio_id, text).json()
    messages = " ".join(e["message"] for e in report["errors"])
    assert "Faltan columnas" in messages
    assert "No se adivinan" in messages


def test_semicolon_files_are_refused_with_an_explanation(client, portfolio_id):
    text = HEADER.replace(",", ";") + "\n2026-01-01;DEPOSIT;;;;100;0;COP;1;;\n"
    report = post(client, portfolio_id, text).json()
    assert "coma" in report["errors"][0]["message"]


def test_empty_file_and_blank_lines(client, portfolio_id):
    report = post(client, portfolio_id, HEADER + "\n\n\n").json()
    assert report["errors"] and "no tiene filas" in report["errors"][0]["message"]


def test_verified_asset_currency_must_match(client, portfolio_id, db_of):
    import datetime as dt

    verified = dt.datetime.now(dt.UTC)
    db_of.add(Asset(symbol="ECOPETROL.CL", currency="COP", last_verified_at=verified))
    db_of.commit()
    wrong = "2026-02-01,BUY,ECOPETROL.CL,10,2000,,0,USD,4000,,"
    report = post(client, portfolio_id, csv_of(wrong)).json()
    assert "no coincide" in report["errors"][0]["message"]

    # Sin divisa la toma del catálogo, y en la divisa base no hace falta FX.
    ok = "2026-02-01,BUY,ECOPETROL.CL,10,2000,,0,,,,"
    good = post(client, portfolio_id, csv_of(ok)).json()
    assert good["errors"] == [] and good["new_rows"] == 1


def test_negative_cash_is_a_warning_not_an_error(client, portfolio_id):
    report = post(client, portfolio_id, csv_of(BUY)).json()
    assert report["errors"] == []
    assert any("efectivo" in w and "negativo" in w for w in report["warnings"])


def test_new_symbols_are_announced(client, portfolio_id):
    report = post(client, portfolio_id, csv_of(DEPOSIT, BUY)).json()
    assert report["new_symbols"] == ["AAPL"]


def test_explicit_external_id_is_respected_and_repeats_are_rejected(client, portfolio_id):
    a = "2026-01-15,DEPOSIT,,,,100,0,COP,1,,ID-1"
    b = "2026-01-16,DEPOSIT,,,,100,0,COP,1,,ID-1"
    report = post(client, portfolio_id, csv_of(a, b)).json()
    assert report["errors"] and "repetido" in report["errors"][0]["message"]


def test_unknown_portfolio_is_404_and_payload_is_validated(client):
    assert post(client, 999, csv_of(DEPOSIT)).status_code == 404


def test_utf8_bom_is_tolerated(client, portfolio_id):
    report = post(client, portfolio_id, "﻿" + csv_of(DEPOSIT)).json()
    assert report["errors"] == [] and report["new_rows"] == 1


def test_ledger_error_names_the_symbol_not_an_internal_id(client, portfolio_id):
    text = csv_of(
        "2026-09-01,BUY,AAPL,5,90,,0,USD,4000,,",
        "2026-09-02,SELL,AAPL,50,95,,0,USD,4000,,",
    )
    message = post(client, portfolio_id, text).json()["errors"][0]["message"]
    assert "AAPL" in message
    assert "activo " not in message
