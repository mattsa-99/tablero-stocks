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
    # RENTA FIJA DIRECTA: un TES, un CDT o una FIC concretos.
    #
    # Es el único tipo que NO viene de un proveedor: se carga a mano. Yahoo no
    # cotiza ninguno, y no es una carencia que vaya a arreglarse: un CDT de
    # Bancolombia a 360 días al 12,3% es un contrato entre dos partes, no un
    # instrumento con mercado. Ver `services/fixed_income.py`.
    FIXED_INCOME = "FIXED_INCOME"
    FUND = "FUND"
    CRYPTO = "CRYPTO"
    OTHER = "OTHER"


class RateKind(StrEnum):
    """Cómo se determina la tasa de un instrumento de renta fija.

    Importa porque cambia qué riesgo se corre. Una tasa FIJA te protege si las
    tasas bajan y te perjudica si suben; una indexada hace lo contrario. Y la
    indexada a UVR es la única que garantiza una rentabilidad REAL, porque la
    unidad misma se ajusta con la inflación.
    """

    FIXED = "FIXED"      # tasa efectiva anual pactada
    IBR = "IBR"          # IBR + spread
    DTF = "DTF"          # DTF + spread
    UVR = "UVR"          # UVR + spread (rentabilidad real)
    IPC = "IPC"          # IPC + spread


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
