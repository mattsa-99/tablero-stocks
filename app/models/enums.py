from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum as SAEnum


class TransactionType(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    # Reservados: el modelo los acepta, la v1 no los procesa todavía.
    FEE = "FEE"
    SPLIT = "SPLIT"

    @property
    def is_trade(self) -> bool:
        return self in (TransactionType.BUY, TransactionType.SELL)

    @property
    def is_cash_event(self) -> bool:
        return self in (
            TransactionType.DIVIDEND,
            TransactionType.DEPOSIT,
            TransactionType.WITHDRAWAL,
        )

    @property
    def is_supported(self) -> bool:
        """FEE y SPLIT están modelados pero no implementados en la v1."""
        return self.is_trade or self.is_cash_event


class AssetType(StrEnum):
    STOCK = "STOCK"
    ETF = "ETF"
    ADR = "ADR"
    REIT = "REIT"
    FUND = "FUND"
    CRYPTO = "CRYPTO"
    OTHER = "OTHER"


class JournalKind(StrEnum):
    """Qué decidiste sobre una empresa, en palabras tuyas."""

    WATCH = "WATCH"  # en vigilancia: aún no compras
    BUY = "BUY"  # comprada (o decidida a comprar)
    HOLD = "HOLD"  # decidiste mantener
    SELL = "SELL"  # vendida o decidida a vender
    PASS = "PASS"  # descartada: mirada y rechazada a propósito


def sa_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """Enum persistido como VARCHAR + CHECK, nunca como ENUM nativo.

    En PostgreSQL, añadir un valor a un ENUM nativo exige ``ALTER TYPE`` fuera
    de transacción; en SQLite el tipo no existe. Con VARCHAR + CHECK, añadir
    ``BOND`` es una migración trivial en ambos motores.
    """
    return SAEnum(
        enum_cls,
        native_enum=False,
        create_constraint=True,  # en SQLAlchemy 2.x el default es False
        length=20,
        validate_strings=True,
        values_callable=lambda e: [member.value for member in e],
        name=name,
    )
