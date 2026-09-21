"""VaultStore write-path invariants: mirror atomicity, staleness, validation."""

import struct
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from octave.db.config import DbConfig
from octave.db.errors import DbConfigError, DbDimensionMismatchError
from octave.db.models import Base, User
from octave.db.sqlite_adapter import SqliteVecAdapter, vector_table_name
from octave.db.types import VaultKind

DIM = 4


def _unit(i: int) -> list[float]:
    return [1.0 if j == i else 0.0 for j in range(DIM)]


def _blob(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


@pytest_asyncio.fixture
async def env(
    tmp_path: Path,
) -> AsyncIterator[tuple[SqliteVecAdapter, async_sessionmaker]]:
    config = DbConfig(
        adapter="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'store.db'}",
        embedding_dim=DIM,
    )
    adapter = SqliteVecAdapter(config)
    engine: AsyncEngine = adapter.make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await adapter.ensure_vector_store(conn)
    factory = adapter.make_session_factory(engine)
    async with factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(User(id="u_2", display_name="Bob"))
        await session.commit()
    yield adapter, factory
    await engine.dispose()


async def test_upsert_without_embedding_persists_row(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        item = await store.upsert(
            item_id="v_1", user_id="u_1", kind=VaultKind.PREFERENCE,
            name="ui.theme", content="The user prefers a dark UI theme.",
        )
        await session.commit()
        assert item.embedding is None
        assert item.embedding_dim is None
        assert item.embedding_model is None
        hits = await store.search(user_id="u_1", embedding=_unit(0))
        assert hits == []  # findable by CRUD, invisible to search


async def test_upsert_with_embedding_writes_cache_and_mirror(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        item = await store.upsert(
            item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
            name="greet", content="Warm greetings.",
            embedding=_unit(0), embedding_model="test-model",
        )
        await session.commit()
        assert item.embedding == _blob(_unit(0))
        assert item.embedding_model == "test-model"
        assert item.embedding_dim == DIM
        hits = await store.search(user_id="u_1", embedding=_unit(0))
        assert [hit.item.id for hit in hits] == ["v_1"]


async def test_upsert_content_change_without_embedding_drops_from_index(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        await store.upsert(
            item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
            name="greet", content="v1", embedding=_unit(0),
        )
        await store.upsert(  # content changed, no new vector -> cache invalid
            item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
            name="greet", content="v2",
        )
        await session.commit()
        item = await store.get("v_1")
        assert item is not None
        assert item.content == "v2"
        assert item.embedding is None
        hits = await store.search(user_id="u_1", embedding=_unit(0))
        assert hits == []
        vec_count = (
            await session.execute(
                text(f"SELECT count(*) FROM {vector_table_name(DIM)}")
            )
        ).scalar_one()
        assert vec_count == 0


async def test_upsert_rejects_wrong_dim_before_writing(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        with pytest.raises(DbDimensionMismatchError):
            await store.upsert(
                item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
                name="x", content="c", embedding=[0.5] * 3,
            )
        assert await store.get("v_1") is None  # nothing written


async def test_upsert_rejects_embedding_model_without_embedding(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        with pytest.raises(DbConfigError):
            await store.upsert(
                item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
                name="x", content="c", embedding_model="test-model",
            )


async def test_upsert_rejects_bad_kind_and_non_mapping_meta(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        with pytest.raises(ValueError):
            await store.upsert(
                item_id="v_1", user_id="u_1", kind="agent_state",  # type: ignore[arg-type]
                name="x", content="c",
            )
        with pytest.raises(DbConfigError):
            await store.upsert(
                item_id="v_2", user_id="u_1", kind=VaultKind.SKILL,
                name="x", content="c", meta=["not", "a", "mapping"],  # type: ignore[arg-type]
            )


async def test_delete_removes_row_and_mirror(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        await store.upsert(
            item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
            name="x", content="c", embedding=_unit(0),
        )
        assert await store.delete("v_1") is True
        assert await store.delete("v_1") is False
        await session.commit()
        assert await store.get("v_1") is None
        hits = await store.search(user_id="u_1", embedding=_unit(0))
        assert hits == []


async def test_search_returns_hits_in_distance_order_with_user_scoping(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultHit, VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        await store.upsert(
            item_id="v_far", user_id="u_1", kind=VaultKind.SKILL,
            name="far", content="far text", embedding=_unit(1),
        )
        await store.upsert(
            item_id="v_near", user_id="u_1", kind=VaultKind.SKILL,
            name="near", content="near text", embedding=_unit(0),
        )
        await store.upsert(
            item_id="v_bob", user_id="u_2", kind=VaultKind.SKILL,
            name="bob", content="bob text", embedding=_unit(0),
        )
        await session.commit()
        hits = await store.search(user_id="u_1", embedding=_unit(0), limit=10)
        assert isinstance(hits[0], VaultHit)
        assert [hit.item.id for hit in hits] == ["v_near", "v_far"]
        assert hits[0].distance <= hits[1].distance
        bob_hits = await store.search(user_id="u_2", embedding=_unit(0))
        assert [hit.item.id for hit in bob_hits] == ["v_bob"]


async def test_search_scoped_by_kind_and_session_id(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        await store.upsert(
            item_id="r_1", user_id="u_1", kind=VaultKind.RUN_RECORD,
            name="chunk", content="verbatim",
            meta={"session_id": "sess_A"}, embedding=_unit(0),
        )
        await store.upsert(
            item_id="r_2", user_id="u_1", kind=VaultKind.RUN_RECORD,
            name="chunk", content="other run",
            meta={"session_id": "sess_B"}, embedding=_unit(0),
        )
        await session.commit()
        hits = await store.search(
            user_id="u_1", embedding=_unit(0),
            kind=VaultKind.RUN_RECORD, session_id="sess_A",
        )
        assert [hit.item.id for hit in hits] == ["r_1"]


async def test_list_items_filters_and_pages(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        for i in range(3):
            await store.upsert(
                item_id=f"p_{i}", user_id="u_1", kind=VaultKind.PREFERENCE,
                name=f"k{i}", content=f"c{i}",
            )
        await store.upsert(
            item_id="s_9", user_id="u_1", kind=VaultKind.SKILL,
            name="s", content="c",
        )
        await session.commit()
        prefs = await store.list_items(user_id="u_1", kind=VaultKind.PREFERENCE)
        assert {item.id for item in prefs} == {"p_0", "p_1", "p_2"}
        page = await store.list_items(user_id="u_1", limit=2, offset=0)
        assert len(page) == 2
        all_items = await store.list_items(user_id="u_1")
        assert len(all_items) == 4


async def test_rollback_leaves_vec_table_and_row_clean(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    """Row + mirror share the session transaction: rollback undoes BOTH."""
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        await store.upsert(
            item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
            name="x", content="c", embedding=_unit(0),
        )
        await session.rollback()

    async with factory() as session:
        store = VaultStore(adapter, session)
        assert await store.get("v_1") is None
        vec_count = (
            await session.execute(
                text(f"SELECT count(*) FROM {vector_table_name(DIM)}")
            )
        ).scalar_one()
        assert vec_count == 0
