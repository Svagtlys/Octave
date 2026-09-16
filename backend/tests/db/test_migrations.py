"""Migrations run programmatically against a temp SQLite file."""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect

from octave.db.errors import DbMigrationError
from octave.db.migrations import current, upgrade

EXPECTED_TABLES = {
    "users",
    "agents",
    "participants",
    "sessions",
    "session_participants",
    "events",
    "mcp_servers",
    "vault_items",
}


def test_upgrade_creates_all_tables(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade(url)
    engine = create_engine(url)
    try:
        assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    upgrade(url)
    upgrade(url)  # second run must not raise
    engine = create_engine(url)
    try:
        assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_current_reports_head(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'm.db'}"
    assert current(url) is None
    upgrade(url)
    assert current(url) is not None


def test_upgrade_bad_url_raises_db_migration_error() -> None:
    with pytest.raises(DbMigrationError):
        upgrade("sqlite:////nonexistent-dir-42/x.db")


def test_migrated_schema_matches_models(tmp_path: Path) -> None:
    """The migration chain and the ORM must describe one schema.

    Catches autogenerate silently dropping CHECKs or composite FKs.
    """
    from octave.db.models import Base

    migrated_url = f"sqlite:///{tmp_path / 'eq.db'}"
    upgrade(migrated_url)
    migrated_engine = create_engine(migrated_url)

    model_url = f"sqlite:///{tmp_path / 'model.db'}"
    model_engine = create_engine(model_url)
    Base.metadata.create_all(model_engine)

    try:
        migrated = inspect(migrated_engine)
        model = inspect(model_engine)
        # ``alembic_version`` exists only in the migrated DB — exclude it.
        migrated_tables = set(migrated.get_table_names()) - {"alembic_version"}
        model_tables = set(model.get_table_names())
        assert migrated_tables == model_tables, (
            f"table set differs: migration={sorted(migrated_tables)} "
            f"models={sorted(model_tables)}"
        )
        for table in sorted(model_tables):
            assert (
                _columns(migrated, table) == _columns(model, table)
            ), f"columns differ for {table}"
            assert (
                _checks(migrated, table) == _checks(model, table)
            ), f"CHECK constraints differ for {table}"
            assert (
                _fks(migrated, table) == _fks(model, table)
            ), f"foreign keys differ for {table}"
    finally:
        migrated_engine.dispose()
        model_engine.dispose()


def _columns(insp: Any, table: str) -> set[tuple[str, str]]:
    return {(c["name"], str(c["type"])) for c in insp.get_columns(table)}


def _checks(insp: Any, table: str) -> set[str]:
    return {c["name"] for c in insp.get_check_constraints(table)}


def _fks(insp: Any, table: str) -> set[tuple[str, ...]]:
    """SQLite does not persist FK constraint names, so ``name`` is normalised
    to ``""`` on both sides — the comparison then checks columns + target."""
    return {
        (fk["name"] or "", tuple(fk["constrained_columns"]), fk["referred_table"])
        for fk in insp.get_foreign_keys(table)
    }
