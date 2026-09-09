"""Motor de P&L: reproduce el ledger para derivar posiciones.

Lógica PURA: no toca la base de datos ni la red. Recibe transacciones ya
cargadas y devuelve estado. Esto es lo que permite testear el corazón
financiero del sistema con datos sintéticos y sin fixtures de infraestructura.

Método contable: COSTE MEDIO PONDERADO (decidido en la Fase 1). Alineado con el
costo promedio que toma como referencia el Estatuto Tributario colombiano para
acciones. Aun así, esto mide RENDIMIENTO, no genera declaraciones fiscales.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app.core.exceptions import InvalidLedgerOperation
from app.core.money import ZERO
from app.models import Transaction, TransactionType

# Una posición se considera cerrada por debajo de este umbral. La división
# Decimal no es exacta (100/3 no termina), así que tras vender todo siempre
# queda un residuo; sin este umbral las posiciones cerradas nunca desaparecen.
QUANTITY_EPSILON = Decimal("1E-8")


@dataclass
class AssetPosition:
    """Estado acumulado de un activo dentro de un portafolio.

    Todos los importes están en la DIVISA BASE del portafolio: la conversión se
    aplica al leer cada transacción, con el fx congelado en ella.
    """

    asset_id: int
    quantity: Decimal = ZERO
    total_cost: Decimal = ZERO  # coste de lo que se mantiene AHORA
    total_invested: Decimal = ZERO  # capital desplegado histórico, nunca baja
    realized_pnl: Decimal = ZERO
    dividend_income: Decimal = ZERO

    @property
    def average_cost(self) -> Decimal:
        if self.quantity <= ZERO:
            return ZERO
        return self.total_cost / self.quantity

    @property
    def is_open(self) -> bool:
        return self.quantity > QUANTITY_EPSILON


@dataclass
class LedgerReplay:
    """Resultado de reproducir el ledger completo de un portafolio."""

    positions: dict[int, AssetPosition] = field(default_factory=dict)
    cash_balance: Decimal = ZERO
    net_invested: Decimal = ZERO  # depósitos - retiradas
    warnings: list[str] = field(default_factory=list)

    @property
    def open_positions(self) -> list[AssetPosition]:
        return [p for p in self.positions.values() if p.is_open]

    @property
    def realized_pnl(self) -> Decimal:
        return sum((p.realized_pnl for p in self.positions.values()), ZERO)

    @property
    def dividend_income(self) -> Decimal:
        return sum((p.dividend_income for p in self.positions.values()), ZERO)

    @property
    def total_cost(self) -> Decimal:
        return sum((p.total_cost for p in self.open_positions), ZERO)


def _base(amount: Decimal, fx: Decimal) -> Decimal:
    return amount * fx


def replay_ledger(
    transactions: Iterable[Transaction], *, strict: bool = False
) -> LedgerReplay:
    """Reproduce las transacciones en orden cronológico.

    El orden es ``(executed_at, id)``. El desempate por id es necesario para
    que el resultado sea determinista cuando dos operaciones comparten
    timestamp: sin él, una compra y una venta simultáneas podrían procesarse en
    cualquier orden y producir P&L distintos entre llamadas.

    Args:
        strict: si True, vender más de lo disponible lanza excepción. Se usa al
            VALIDAR una escritura. En los caminos de LECTURA se pasa False: un
            ledger ya inconsistente no debe dejar el dashboard inservible; se
            acota la venta y se registra un aviso.
    """
    ordered: Sequence[Transaction] = sorted(
        transactions, key=lambda t: (t.executed_at, t.id or 0)
    )
    result = LedgerReplay()

    for tx in ordered:
        fx = tx.fx_rate_to_base
        fees = _base(tx.fees, fx)

        if tx.type is TransactionType.BUY:
            position = result.positions.setdefault(tx.asset_id, AssetPosition(tx.asset_id))
            cost = _base(tx.quantity * tx.price, fx) + fees
            position.quantity += tx.quantity
            position.total_cost += cost
            position.total_invested += cost
            result.cash_balance -= cost

        elif tx.type is TransactionType.SELL:
            position = result.positions.setdefault(tx.asset_id, AssetPosition(tx.asset_id))
            quantity = tx.quantity

            if quantity > position.quantity:
                message = (
                    f"Venta de {quantity} unidades del activo {tx.asset_id} el "
                    f"{tx.executed_at.date()} supera las {position.quantity} disponibles"
                )
                if strict:
                    raise InvalidLedgerOperation(message)
                result.warnings.append(message + " (acotada al disponible)")
                quantity = position.quantity

            if quantity > ZERO:
                # El coste medio se captura ANTES de mutar la posición: vender
                # no cambia el coste medio de lo que queda.
                cost_removed = position.average_cost * quantity
                proceeds = _base(quantity * tx.price, fx) - fees
                position.realized_pnl += proceeds - cost_removed
                position.quantity -= quantity
                position.total_cost -= cost_removed
                result.cash_balance += proceeds

            _close_if_residual(position)

        elif tx.type is TransactionType.DIVIDEND:
            position = result.positions.setdefault(tx.asset_id, AssetPosition(tx.asset_id))
            # Neto de retención en la fuente. NO reduce el coste base: mezclarlo
            # falsearía el coste medio y el P&L no realizado, e impediría
            # separar rentabilidad por precio de rentabilidad por dividendo.
            net = _base(tx.cash_amount, fx) - fees
            position.dividend_income += net
            result.cash_balance += net

        elif tx.type is TransactionType.DEPOSIT:
            gross = _base(tx.cash_amount, fx)
            result.cash_balance += gross - fees
            result.net_invested += gross

        elif tx.type is TransactionType.WITHDRAWAL:
            gross = _base(tx.cash_amount, fx)
            result.cash_balance -= gross + fees
            result.net_invested -= gross

        else:
            result.warnings.append(f"Tipo {tx.type} no procesado en esta versión")

    return result


def _close_if_residual(position: AssetPosition) -> None:
    """Cierra la posición cuando la cantidad restante es residuo de redondeo.

    El coste sobrante se imputa al P&L realizado en lugar de descartarse: de lo
    contrario los céntimos desaparecerían y la suma de P&L dejaría de cuadrar
    con el efectivo movido.
    """
    if position.quantity <= QUANTITY_EPSILON:
        position.realized_pnl -= position.total_cost
        position.quantity = ZERO
        position.total_cost = ZERO


def validate_new_transaction(
    existing: Iterable[Transaction], candidate: Transaction
) -> None:
    """Comprueba que añadir `candidate` deja el ledger en un estado válido.

    Se reproduce el ledger COMPLETO con la candidata incluida, no solo se
    compara contra la posición actual. Es imprescindible porque una
    transacción puede insertarse con fecha PASADA: una compra de hoy no
    legitima una venta de hace un mes.
    """
    replay_ledger([*existing, candidate], strict=True)
