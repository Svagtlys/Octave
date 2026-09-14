"""Migrations run programmatically against a temp SQLite file."""

from pathlib import Path

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
