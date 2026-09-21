"""SqliteVecAdapter: extension loading, vector-store lifecycle, dim change."""

import logging
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlite_vec import serialize_float32

from octave.db.config import DbConfig
from octave.db.errors import DbDimensionMismatchError
from octave.db.models import Base, User, VaultItem
from octave.db.sqlite_adapter import SqliteVecAdapter, vector_table_name
from tests.db.conformance import run_db_adapter_conformance


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


async def test_search_similar_ranks_nearest_first(tmp_path: Path) -> None:
    config = _config(tmp_path, dim=3)
    adapter = SqliteVecAdapter(config)
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await adapter.ensure_vector_store(conn)
        factory = adapter.make_session_factory(engine)
        async with factory() as session:
            session.add(User(id="u_1", display_name="Alice"))
            await session.commit()
        vectors = {
            "v_east": [1.0, 0.0, 0.0],
            "v_near_east": [0.9, 0.1, 0.0],
            "v_north": [0.0, 1.0, 0.0],
        }
        async with engine.begin() as conn:
            for item_id, vector in vectors.items():
                await conn.execute(
                    VaultItem.__table__.insert().values(
                        id=item_id, user_id="u_1", kind="skill",
                        name=item_id, content=item_id,
                        # Core insert bypasses ORM defaults — supply the
                        # NOT NULL JSON column explicitly. Column is named
                        # ``metadata`` in SQL (ORM attribute is ``meta``).
                        metadata={}, embedding_dim=3,
                    )
                )
                await conn.execute(
                    text(
                        f"INSERT INTO {vector_table_name(3)}"
                        "(item_id, embedding, kind, user_id, session_id) "
                        "VALUES (:id, :vec, '', '', '')"
                    ),
                    {"id": item_id, "vec": serialize_float32(vector)},
                )
            hits = await adapter.search_similar(conn, [1.0, 0.0, 0.0], limit=2)
        assert [hit.item_id for hit in hits] == ["v_east", "v_near_east"]
        assert hits[0].distance <= hits[1].distance
    finally:
        await engine.dispose()


async def test_search_similar_rejects_wrong_dim(tmp_path: Path) -> None:
    adapter = SqliteVecAdapter(_config(tmp_path, dim=3))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await adapter.ensure_vector_store(conn)
            with pytest.raises(DbDimensionMismatchError) as exc:
                await adapter.search_similar(conn, [1.0, 0.0], limit=1)
        assert exc.value.expected == 3
        assert exc.value.actual == 2
    finally:
        await engine.dispose()


async def test_sqlite_adapter_passes_conformance(tmp_path: Path) -> None:
    """Dim 2 keeps the hand-rolled conformance encoding cheap to write."""
    adapter = SqliteVecAdapter(_config(tmp_path, dim=2))
    engine = adapter.make_engine()
    try:
        await run_db_adapter_conformance(adapter, engine)
    finally:
        await engine.dispose()


async def test_store_vector_round_trip_and_aux_columns(tmp_path: Path) -> None:
    """store_vector writes aux columns; search finds it; remove_vector drops it."""
    adapter = SqliteVecAdapter(_config(tmp_path, dim=3))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await adapter.ensure_vector_store(conn)
            await adapter.store_vector(
                conn,
                item_id="s_1",
                embedding=[1.0, 0.0, 0.0],
                kind="prompt",
                user_id="u_1",
                session_id="sess_A",
            )
            hits = await adapter.search_similar(conn, [1.0, 0.0, 0.0], limit=5)
            assert [hit.item_id for hit in hits] == ["s_1"]
            aux = (
                await conn.execute(
                    text(
                        f"SELECT kind, user_id, session_id "
                        f"FROM {vector_table_name(3)} WHERE item_id = 's_1'"
                    )
                )
            ).one()
            assert tuple(aux) == ("prompt", "u_1", "sess_A")
            await adapter.remove_vector(conn, item_id="s_1")
            hits = await adapter.search_similar(conn, [1.0, 0.0, 0.0], limit=5)
            assert hits == []
    finally:
        await engine.dispose()


async def test_store_vector_is_idempotent_replace(tmp_path: Path) -> None:
    """Storing the same item_id twice keeps ONE row, latest vector wins."""
    adapter = SqliteVecAdapter(_config(tmp_path, dim=2))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await adapter.ensure_vector_store(conn)
            await adapter.store_vector(
                conn, item_id="s_1", embedding=[1.0, 0.0], kind="skill", user_id="u_1"
            )
            await adapter.store_vector(
                conn, item_id="s_1", embedding=[0.0, 1.0], kind="skill", user_id="u_1"
            )
            rows = (
                await conn.execute(
                    text(f"SELECT count(*) FROM {vector_table_name(2)}")
                )
            ).scalar_one()
            hits = await adapter.search_similar(conn, [0.0, 1.0], limit=5)
        assert rows == 1
        assert [hit.item_id for hit in hits] == ["s_1"]
    finally:
        await engine.dispose()


async def test_store_vector_rejects_wrong_dim(tmp_path: Path) -> None:
    adapter = SqliteVecAdapter(_config(tmp_path, dim=3))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await adapter.ensure_vector_store(conn)
            with pytest.raises(DbDimensionMismatchError):
                await adapter.store_vector(conn, item_id="s_1", embedding=[1.0, 0.0])
    finally:
        await engine.dispose()


async def test_remove_vector_unknown_id_is_silent(tmp_path: Path) -> None:
    adapter = SqliteVecAdapter(_config(tmp_path, dim=2))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await adapter.ensure_vector_store(conn)
            await adapter.remove_vector(conn, item_id="ghost")  # must not raise
    finally:
        await engine.dispose()


async def test_search_similar_filters_are_exact_pre_k(tmp_path: Path) -> None:
    """Filters apply INSIDE the KNN scan: with k=1 the matching item is found
    even when a closer non-matching item exists. Post-k filtering would
    return zero hits here."""
    adapter = SqliteVecAdapter(_config(tmp_path, dim=2))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await adapter.ensure_vector_store(conn)
            await adapter.store_vector(
                conn, item_id="near_skill", embedding=[1.0, 0.0],
                kind="skill", user_id="u_1",
            )
            await adapter.store_vector(
                conn, item_id="far_prompt", embedding=[0.707, 0.707],
                kind="prompt", user_id="u_1", session_id="sess_A",
            )
            hits = await adapter.search_similar(
                conn, [1.0, 0.0], limit=1, kind="prompt"
            )
            assert [hit.item_id for hit in hits] == ["far_prompt"]
            hits = await adapter.search_similar(
                conn, [1.0, 0.0], limit=5, user_id="u_1", session_id="sess_A"
            )
            assert [hit.item_id for hit in hits] == ["far_prompt"]
            hits = await adapter.search_similar(
                conn, [1.0, 0.0], limit=5, session_id="sess_B"
            )
            assert hits == []
            hits = await adapter.search_similar(conn, [1.0, 0.0], limit=5)
            assert [hit.item_id for hit in hits] == ["near_skill", "far_prompt"]
    finally:
        await engine.dispose()
