"""Tests de las migraciones.

Protegen dos cosas que se rompen en silencio: que las migraciones describan
exactamente los modelos, y que se puedan deshacer.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from alembic import command
from app.db.alembic_support import COMPARE_OPTIONS
from app.db.registry import Base

PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_TABLES = {
    "portfolios",
    "assets",
    "transactions",
    "price_history",
    "asset_quotes",
    "fundamental_snapshots",
    "fx_rates",
    "data_sync_state",
    "journal_entries",
}


@pytest.fixture
def alembic_config():
    """Config de Alembic apuntando a una base de datos temporal."""
    with tempfile.TemporaryDirectory() as tmp:
        url = f"sqlite:///{Path(tmp) / 'migrations.db'}"
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
        cfg.set_main_option("sqlalchemy.url", url)
        yield cfg, url


def test_upgrade_creates_all_tables(alembic_config):
    cfg, url = alembic_config
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert tables >= EXPECTED_TABLES


def test_no_drift_between_models_and_migrations(alembic_config):
    """Autogenerate no debe detectar NADA tras aplicar las migraciones.

    Es el test que evita que un cambio en un modelo se olvide de su migración,
    y el que detecta la regresión de los CHECK generados por los enums: sin el
    filtro de `alembic_support`, aquí aparecen dos "remove_constraint"
    espurios que borrarían la validación de dominio de `type` y `asset_type`.
    """
    cfg, url = alembic_config
    command.upgrade(cfg, "head")

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            migration_ctx = MigrationContext.configure(
                connection, opts={"render_as_batch": True, **COMPARE_OPTIONS}
            )
            diff = compare_metadata(migration_ctx, Base.metadata)
    finally:
        engine.dispose()

    assert diff == [], f"Los modelos y las migraciones han divergido: {diff}"


def test_downgrade_round_trip(alembic_config):
    """downgrade debe dejar la base limpia y upgrade volver a construirla."""
    cfg, url = alembic_config

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(url)
    try:
        after_downgrade = set(inspect(engine).get_table_names())
        assert not (EXPECTED_TABLES & after_downgrade)

        command.upgrade(cfg, "head")
        after_upgrade = set(inspect(engine).get_table_names())
        assert after_upgrade >= EXPECTED_TABLES
    finally:
        engine.dispose()


def test_single_migration_head(alembic_config):
    """Una sola cabeza: ramas de migración solo generan conflictos."""
    from alembic.script import ScriptDirectory

    cfg, _ = alembic_config
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1, f"Se esperaba una sola cabeza, hay {len(heads)}: {heads}"
