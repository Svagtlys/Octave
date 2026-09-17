"""db_lifespan: startup wiring, auto-migrate gate, fail-fast, teardown."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, inspect

from octave.db.errors import DbMigrationError, UnknownDbAdapterError
from octave.db.lifespan import db_lifespan
from octave.db.migrations import current, upgrade
from octave.db.sqlite_adapter import vector_table_name


@pytest.fixture
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Hermetic OCTAVE_DB_* env: chdir so no local .env leaks in, clear the
    vars, then point the URL at a temp file. Yields the database path."""
    monkeypatch.chdir(tmp_path)
    for var in (
        "OCTAVE_DB_URL",
        "OCTAVE_DB_ADAPTER",
        "OCTAVE_DB_EMBEDDING_DIM",
        "OCTAVE_DB_AUTO_MIGRATE",
    ):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "life.db"
    monkeypatch.setenv("OCTAVE_DB_URL", f"sqlite+aiosqlite:///{path}")
    yield path


async def test_startup_migrates_ensures_vector_store_and_populates_state(
    db_env: Path,
) -> None:
    app = FastAPI()
    async with db_lifespan(app):
        assert current(f"sqlite:///{db_env}") is not None
        assert app.state.db_session_factory is not None
        assert app.state.db_adapter is not None
        assert app.state.db_engine is not None
    # Vector index created at the configured dim; relational schema too.
    engine = create_engine(f"sqlite:///{db_env}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert vector_table_name(768) in tables
    assert {"users", "vault_items"} <= tables


async def test_auto_migrate_false_unmigrated_raises(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTAVE_DB_AUTO_MIGRATE", "false")
    app = FastAPI()
    with pytest.raises(DbMigrationError, match="alembic upgrade head"):
        async with db_lifespan(app):
            pass  # never reached


async def test_auto_migrate_false_pre_migrated_boots_without_upgrade(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_url = f"sqlite:///{db_env}"
    upgrade(sync_url)
    monkeypatch.setenv("OCTAVE_DB_AUTO_MIGRATE", "false")

    def _explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("upgrade() must not run when auto_migrate=false")

    monkeypatch.setattr("octave.db.lifespan.upgrade", _explode)
    app = FastAPI()
    async with db_lifespan(app):
        assert app.state.db_session_factory is not None


async def test_unknown_adapter_propagates(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTAVE_DB_ADAPTER", "no_such_adapter")
    app = FastAPI()
    with pytest.raises(UnknownDbAdapterError):
        async with db_lifespan(app):
            pass  # never reached


async def test_restart_is_idempotent(db_env: Path) -> None:
    """Full start → stop → start on the same file. Proves teardown released
    the engine (no leaked handle) and setup is idempotent (upgrade() is
    Alembic-idempotent; ensure_vector_store() sees the existing index)."""
    for _ in range(2):
        app = FastAPI()
        async with db_lifespan(app):
            assert app.state.db_session_factory is not None
    assert current(f"sqlite:///{db_env}") is not None
