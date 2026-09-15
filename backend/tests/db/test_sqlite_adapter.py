"""SqliteVecAdapter: extension loading, vector-store lifecycle, dim change."""

import logging
from pathlib import Path

import pytest
from sqlalchemy import text

from octave.db.config import DbConfig
from octave.db.models import Base, User
from octave.db.sqlite_adapter import SqliteVecAdapter, vector_table_name


def _config(tmp_path: Path, dim: int = 4) -> DbConfig:
    return DbConfig(
        adapter="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'vec.db'}",
        embedding_dim=dim,
    )


def test_vector_table_name_is_dim_suffixed() -> None:
    assert vector_table_name(768) == "vec_vault_items_768"


async def test_extension_is_loaded_on_every_connection(
    tmp_path: Path,
) -> None:
    adapter = SqliteVecAdapter(_config(tmp_path))
    engine = adapter.make_engine()
    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("SELECT vec_version()"))).scalar_one()
        assert str(version).startswith("v")
    finally:
        await engine.dispose()


async def test_ensure_vector_store_is_idempotent(tmp_path: Path) -> None:
    adapter = SqliteVecAdapter(_config(tmp_path))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await adapter.ensure_vector_store(conn)
            await adapter.ensure_vector_store(conn)
            names = {
                row[0]
                for row in (
                    await conn.execute(
                        text("SELECT name FROM sqlite_master WHERE type='table'")
                    )
                ).all()
            }
        assert "vec_vault_items_4" in names
    finally:
        await engine.dispose()


async def test_dim_change_drops_stale_index_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    adapter = SqliteVecAdapter(_config(tmp_path, dim=4))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await adapter.ensure_vector_store(conn)
        with caplog.at_level(logging.WARNING, logger="octave.db.sqlite_adapter"):
            rebuilt = SqliteVecAdapter(_config(tmp_path, dim=8))
            async with engine.begin() as conn:
                await rebuilt.ensure_vector_store(conn)
            async with engine.connect() as conn:
                names = {
                    row[0]
                    for row in (
                        await conn.execute(
                            text("SELECT name FROM sqlite_master WHERE type='table'")
                        )
                    ).all()
                }
        assert "vec_vault_items_8" in names
        assert "vec_vault_items_4" not in names
        assert any("re-embed" in record.message for record in caplog.records)
    finally:
        await engine.dispose()


async def test_make_session_factory_keeps_rows_readable(
    tmp_path: Path,
) -> None:
    """``expire_on_commit=False`` — rows stay readable after commit."""
    adapter = SqliteVecAdapter(_config(tmp_path))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = adapter.make_session_factory(engine)
        assert factory.kw["expire_on_commit"] is False
        async with factory() as session:
            created = User(id="u_1", display_name="Alice")
            session.add(created)
            await session.commit()
            # expire_on_commit=False: the instance is still usable here...
            assert created.display_name == "Alice"
            # ...and still readable after a fresh session loads it.
        async with factory() as session:
            loaded = await session.get(User, "u_1")
        assert loaded is not None
        assert loaded.display_name == "Alice"
    finally:
        await engine.dispose()
