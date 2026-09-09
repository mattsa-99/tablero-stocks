"""Tests del motor de P&L.

Los números esperados están calculados a mano en los docstrings. Un test de
P&L que solo comprueba "no revienta" no vale nada: lo que importa es que la
cifra sea exactamente la correcta.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from app.core.exceptions import InvalidLedgerOperation
from app.models import Transaction, TransactionType
from app.services.pnl import replay_ledger

UTC = dt.UTC
BASE = dt.datetime(2026, 1, 15, 15, 0, tzinfo=UTC)


def tx(kind, day, *, asset_id=1, qty=None, price=None, cash=None, fees="0", fx="1", tid=None):
    return Transaction(
        id=tid or day,
        portfolio_id=1,
        asset_id=asset_id if kind not in ("DEPOSIT", "WITHDRAWAL") else None,
        type=TransactionType(kind),
        executed_at=BASE + dt.timedelta(days=day),
        quantity=Decimal(qty) if qty else None,
        price=Decimal(price) if price else None,
        cash_amount=Decimal(cash) if cash else None,
        fees=Decimal(fees),
        currency="USD",
        fx_rate_to_base=Decimal(fx),
    )


# --------------------------------------------------------------------------
# Coste medio ponderado
# --------------------------------------------------------------------------


def test_single_buy():
    """10 @ 100 + 5 de comisión = 1005 de coste, 100.50 por título."""
    replay = replay_ledger([tx("BUY", 1, qty="10", price="100", fees="5")])
    position = replay.positions[1]

    assert position.quantity == Decimal("10")
    assert position.total_cost == Decimal("1005")
    assert position.average_cost == Decimal("100.5")
    assert replay.cash_balance == Decimal("-1005")


def test_weighted_average_across_two_buys():
    """10@100 (1000) + 10@200 (2000) = 3000 / 20 = 150 de coste medio."""
    replay = replay_ledger(
        [tx("BUY", 1, qty="10", price="100"), tx("BUY", 2, qty="10", price="200")]
    )
    position = replay.positions[1]

    assert position.quantity == Decimal("20")
    assert position.average_cost == Decimal("150")
    assert position.total_invested == Decimal("3000")


def test_sell_does_not_change_average_cost():
    """Vender consume coste al precio medio; el medio de lo que queda no cambia."""
    replay = replay_ledger(
        [
            tx("BUY", 1, qty="10", price="100"),
            tx("BUY", 2, qty="10", price="200"),
            tx("SELL", 3, qty="5", price="250"),
        ]
    )
    position = replay.positions[1]

    # Realizado = 5*250 - 5*150 = 1250 - 750 = 500
    assert position.realized_pnl == Decimal("500")
    assert position.average_cost == Decimal("150")
    assert position.quantity == Decimal("15")
    assert position.total_cost == Decimal("2250")


def test_fees_reduce_proceeds_on_sell():
    """Comisiones: capitalizan en la compra, restan del ingreso en la venta."""
    replay = replay_ledger(
        [
            tx("BUY", 1, qty="10", price="100", fees="10"),  # coste 1010, medio 101
            tx("SELL", 2, qty="10", price="120", fees="10"),  # ingreso 1190
        ]
    )
    # 1190 - 1010 = 180
    assert replay.positions[1].realized_pnl == Decimal("180")


def test_full_exit_closes_position():
    replay = replay_ledger(
        [tx("BUY", 1, qty="10", price="100"), tx("SELL", 2, qty="10", price="150")]
    )
    position = replay.positions[1]

    assert position.quantity == Decimal("0")
    assert position.total_cost == Decimal("0")
    assert position.realized_pnl == Decimal("500")
    assert not position.is_open
    assert replay.open_positions == []


def test_non_terminating_division_leaves_no_residue():
    """3 títulos por 100 dan un coste medio periódico (33.333...).

    Sin el cierre por residuo, la posición quedaría abierta con 1E-27 títulos y
    un coste sobrante, y el P&L no cuadraría con el efectivo movido.
    """
    replay = replay_ledger(
        [tx("BUY", 1, qty="3", price="100"), tx("SELL", 2, qty="3", price="100")]
    )
    position = replay.positions[1]

    assert position.quantity == Decimal("0")
    assert position.total_cost == Decimal("0")
    assert position.realized_pnl == Decimal("0")
    assert replay.cash_balance == Decimal("0")


# --------------------------------------------------------------------------
# Dividendos y caja
# --------------------------------------------------------------------------


def test_dividend_does_not_touch_cost_basis():
    """El dividendo es ingreso separado: mezclarlo falsearía el coste medio."""
    replay = replay_ledger(
        [tx("BUY", 1, qty="10", price="100"), tx("DIVIDEND", 2, cash="50", fees="7.5")]
    )
    position = replay.positions[1]

    assert position.dividend_income == Decimal("42.5")
    assert position.average_cost == Decimal("100")
    assert position.total_cost == Decimal("1000")
    assert replay.cash_balance == Decimal("-957.5")


def test_cash_movements():
    replay = replay_ledger(
        [tx("DEPOSIT", 1, cash="10000"), tx("WITHDRAWAL", 2, cash="2500")]
    )
    assert replay.cash_balance == Decimal("7500")
    assert replay.net_invested == Decimal("7500")


def test_full_cash_cycle():
    """Depósito, compra, dividendo y venta: la caja debe cuadrar al céntimo."""
    replay = replay_ledger(
        [
            tx("DEPOSIT", 1, cash="10000"),
            tx("BUY", 2, qty="10", price="100", fees="5"),  # -1005
            tx("DIVIDEND", 3, cash="20"),  # +20
            tx("SELL", 4, qty="10", price="130", fees="5"),  # +1295
        ]
    )
    assert replay.cash_balance == Decimal("10310")
    assert replay.positions[1].realized_pnl == Decimal("290")  # 1295 - 1005
    assert replay.positions[1].dividend_income == Decimal("20")


# --------------------------------------------------------------------------
# Multi-divisa
# --------------------------------------------------------------------------


def test_fx_applied_per_transaction():
    """Cada compra congela SU tipo de cambio; el coste medio los mezcla.

    10@100 a 4000 = 4.000.000 COP
    10@100 a 5000 = 5.000.000 COP
    Total 9.000.000 / 20 = 450.000 COP por título.
    """
    replay = replay_ledger(
        [
            tx("BUY", 1, qty="10", price="100", fx="4000"),
            tx("BUY", 2, qty="10", price="100", fx="5000"),
        ]
    )
    position = replay.positions[1]

    assert position.total_cost == Decimal("9000000")
    assert position.average_cost == Decimal("450000")


def test_fx_gain_is_realized_on_sell():
    """Comprar a 4000 y vender al mismo precio con FX 5000 genera P&L de divisa."""
    replay = replay_ledger(
        [
            tx("BUY", 1, qty="10", price="100", fx="4000"),  # coste 4.000.000
            tx("SELL", 2, qty="10", price="100", fx="5000"),  # ingreso 5.000.000
        ]
    )
    assert replay.positions[1].realized_pnl == Decimal("1000000")


# --------------------------------------------------------------------------
# Orden y validación
# --------------------------------------------------------------------------


def test_replay_orders_by_execution_not_insertion():
    """El orden lo da executed_at, no el orden de inserción ni el id."""
    late_buy = tx("BUY", 1, qty="10", price="100", tid=99)
    early_sell = tx("SELL", 5, qty="10", price="150", tid=1)

    replay = replay_ledger([early_sell, late_buy])
    assert replay.positions[1].realized_pnl == Decimal("500")
    assert replay.warnings == []


def test_oversell_raises_in_strict_mode():
    with pytest.raises(InvalidLedgerOperation, match="supera"):
        replay_ledger(
            [tx("BUY", 1, qty="10", price="100"), tx("SELL", 2, qty="15", price="120")],
            strict=True,
        )


def test_oversell_is_clamped_and_warned_in_read_mode():
    """Un ledger ya inconsistente no debe dejar el dashboard inservible."""
    replay = replay_ledger(
        [tx("BUY", 1, qty="10", price="100"), tx("SELL", 2, qty="15", price="120")],
        strict=False,
    )
    assert len(replay.warnings) == 1
    assert "acotada" in replay.warnings[0]
    assert replay.positions[1].quantity == Decimal("0")


def test_backdated_sell_is_detected():
    """Una compra de hoy NO legitima una venta de la semana pasada."""
    with pytest.raises(InvalidLedgerOperation):
        replay_ledger(
            [tx("SELL", 1, qty="5", price="120"), tx("BUY", 5, qty="10", price="100")],
            strict=True,
        )


def test_realized_pnl_survives_position_close():
    """El P&L realizado sigue contando aunque ya no haya posición abierta."""
    replay = replay_ledger(
        [tx("BUY", 1, qty="10", price="100"), tx("SELL", 2, qty="10", price="150")]
    )
    assert replay.open_positions == []
    assert replay.realized_pnl == Decimal("500")
    assert replay.total_cost == Decimal("0")
