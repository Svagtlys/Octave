"""Session-summary routes (issue #112).

Harness like tests/routes/test_sessions.py, but vec-enabled: VaultStore
upsert/list touch the vec0 mirror, so app.state.db_adapter is a real
SqliteVecAdapter over the same engine as the session factory.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from octave.auth.config import AuthSettings
from octave.auth.passwords import DummyHasher
from octave.db.config import DbConfig
from octave.db.models import Base, Session, VaultItem
from octave.db.session_store import SessionStore
from octave.db.sqlite_adapter import SqliteVecAdapter
from octave.db.types import VaultKind
from octave.db.vault_store import VaultStore
from octave.middleware import add_error_handlers
from octave.routes.auth import router as auth_router
from octave.routes.summaries import router as summaries_router


def _dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


@pytest_asyncio.fixture
async def app_and_factory(tmp_path: Path):
    config = DbConfig(
        adapter="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'summaries_routes.db'}",
        embedding_dim=4,
    )
    adapter = SqliteVecAdapter(config)
    engine = adapter.make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await adapter.ensure_vector_store(conn)
    factory = adapter.make_session_factory(engine)
    app = FastAPI()
    app.state.db_session_factory = factory
    app.state.db_adapter = adapter
    app.state.auth_hasher = DummyHasher()
    app.state.auth_settings = AuthSettings(cookie_secure=False)
    add_error_handlers(app)
    app.include_router(auth_router, prefix="/api")
    app.include_router(summaries_router, prefix="/api")
    yield app, factory
    await engine.dispose()


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _bootstrap(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/bootstrap",
        json={"username": "alice", "password": "pw", "display_name": "Alice"},
    )
    assert response.status_code == 201


async def _alice_id(client: AsyncClient) -> str:
    """The bootstrapped owner's user id (uuid4-hex — never hardcode it)."""
    return (await client.get("/api/auth/me")).json()["id"]


async def _create_session(
    factory, session_id: str, owner: str, *, join: bool = True
) -> None:
    """Seed a session; with ``join`` the owner's participant is a member so
    list_sessions (membership-filtered) can see it."""
    from octave.db.models import SessionParticipant

    async with factory() as session:
        row = Session(id=session_id, created_by_user_id=owner, status="active")
        session.add(row)
        if join:
            participant = await SessionStore(session).get_participant_for_user(owner)
            assert participant is not None
            session.add(
                SessionParticipant(
                    session_id=session_id, participant_id=participant.id
                )
            )
        await session.commit()
        row.created_at = _dt(1)


async def _seed_summary(
    factory, db_adapter, session_id: str, *, user_id: str, name: str, content: str
) -> None:
    async with factory() as session:
        await VaultStore(db_adapter, session).upsert(
            item_id=f"session_summary:{session_id}",
            user_id=user_id,
            kind=VaultKind.SESSION_SUMMARY,
            name=name,
            content=content,
            meta={"session_id": session_id, "covered_seq": 2, "model_name": "m-1"},
        )
        await session.commit()


# -- list ---------------------------------------------------------------------


async def test_list_requires_auth(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        assert (await client.get("/api/session-summaries")).status_code == 401


async def test_list_attaches_summary_or_null(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        alice = await _alice_id(client)
        await _create_session(factory, "s_1", owner=alice)
        await _seed_summary(
            factory,
            app.state.db_adapter,
            "s_1",
            user_id=alice,
            name="Weather",
            content="Sunny day, went outside.",
        )
        await _create_session(factory, "s_2", owner=alice)
        response = await client.get("/api/session-summaries")
    assert response.status_code == 200
    page = response.json()
    by_id = {item["id"]: item for item in page["items"]}
    assert by_id["s_1"]["summary"]["title"] == "Weather"
    assert by_id["s_1"]["summary"]["snippet"].startswith("Sunny")
    assert by_id["s_1"]["summary"]["model_name"] == "m-1"
    assert by_id["s_2"]["summary"] is None
    assert page["total"] == 2


async def test_list_never_leaks_other_users_summaries(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        alice = await _alice_id(client)
        created = await client.post(
            "/api/auth/users",
            json={
                "username": "bob",
                "password": "pw2",
                "display_name": "Bob",
                "role": "member",
            },
        )
        assert created.status_code == 201
        bob = created.json()["id"]
        await _create_session(factory, "s_1", owner=alice)
        # A summary owned by a different user, pointing at alice's session:
        # the owner-scoped join must skip it.
        await _seed_summary(
            factory,
            app.state.db_adapter,
            "s_1",
            user_id=bob,
            name="Foreign",
            content="x",
        )
        page = (await client.get("/api/session-summaries")).json()
    assert page["items"][0]["summary"] is None


async def test_list_skips_malformed_meta_items(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        alice = await _alice_id(client)
        await _create_session(factory, "s_1", owner=alice)
        async with factory() as session:
            session.add(
                VaultItem(
                    id="session_summary:s_1",
                    user_id=alice,
                    kind="session_summary",
                    name="Broken",
                    content="y",
                    meta={"nope": True},
                )
            )
            await session.commit()
        page = (await client.get("/api/session-summaries")).json()
    assert page["items"][0]["summary"] is None


async def test_list_pages_summaries_beyond_one_vault_page(app_and_factory) -> None:
    """150 sessions each with a summary: the vault fetch must page past its
    first page (a silent cap would drop summaries from the join)."""
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        alice = await _alice_id(client)
        async with factory() as session:
            store = VaultStore(app.state.db_adapter, session)
            from octave.db.models import SessionParticipant

            participant = await SessionStore(session).get_participant_for_user(alice)
            assert participant is not None
            for i in range(150):
                sid = f"s_{i:03d}"
                session.add(
                    Session(id=sid, created_by_user_id=alice, status="active")
                )
                session.add(
                    SessionParticipant(session_id=sid, participant_id=participant.id)
                )
                await store.upsert(
                    item_id=f"session_summary:{sid}",
                    user_id=alice,
                    kind=VaultKind.SESSION_SUMMARY,
                    name=f"T{i}",
                    content=f"c{i}",
                    meta={"session_id": sid, "covered_seq": 1},
                )
            await session.commit()
        page = (await client.get("/api/session-summaries?limit=100")).json()
    assert page["total"] == 150
    assert all(item["summary"] is not None for item in page["items"])


async def test_list_pagination_bounds(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        alice = await _alice_id(client)
        for i in range(3):
            await _create_session(factory, f"s_{i}", owner=alice)
        page = (await client.get("/api/session-summaries?limit=2&offset=0")).json()
        bad = await client.get("/api/session-summaries?limit=0")
    assert len(page["items"]) == 2 and page["total"] == 3
    assert (page["limit"], page["offset"]) == (2, 0)
    assert bad.status_code == 422


# -- detail -------------------------------------------------------------------


async def test_detail_requires_auth(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        assert (await client.get("/api/session-summaries/s_1")).status_code == 401


async def test_detail_404_unknown_403_non_member(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        alice = await _alice_id(client)
        assert (
            await client.get("/api/session-summaries/nope")
        ).status_code == 404
        await _create_session(factory, "s_1", owner=alice)
        # bob exists but never joined s_1.
        created = await client.post(
            "/api/auth/users",
            json={
                "username": "bob",
                "password": "pw2",
                "display_name": "Bob",
                "role": "member",
            },
        )
        assert created.status_code == 201
        bob = await _client(app)
        login = await bob.post(
            "/api/auth/login", json={"username": "bob", "password": "pw2"}
        )
        assert login.status_code == 200
        assert (await bob.get("/api/session-summaries/s_1")).status_code == 403
        await bob.aclose()


async def test_detail_returns_null_without_cache(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        alice = await _alice_id(client)
        await _create_session(factory, "s_1", owner=alice)
        response = await client.get("/api/session-summaries/s_1")
    assert response.status_code == 200
    assert response.json() == {"session_id": "s_1", "summary": None}


async def test_detail_returns_full_content_from_cache(app_and_factory) -> None:
    app, factory = app_and_factory
    long_body = "x" * 300
    async with await _client(app) as client:
        await _bootstrap(client)
        alice = await _alice_id(client)
        await _create_session(factory, "s_1", owner=alice)
        await _seed_summary(
            factory,
            app.state.db_adapter,
            "s_1",
            user_id=alice,
            name="Weather",
            content=long_body,
        )
        response = await client.get("/api/session-summaries/s_1")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["title"] == "Weather"
    assert body["summary"]["content"] == long_body  # full, not truncated
    assert body["summary"]["model_name"] == "m-1"


async def test_detail_never_calls_inference(app_and_factory) -> None:
    """No app.state.inference_adapter at all: peek still answers 200."""
    app, factory = app_and_factory
    assert not hasattr(app.state, "inference_adapter")
    async with await _client(app) as client:
        await _bootstrap(client)
        alice = await _alice_id(client)
        await _create_session(factory, "s_1", owner=alice)
        await _seed_summary(
            factory,
            app.state.db_adapter,
            "s_1",
            user_id=alice,
            name="Weather",
            content="Sunny.",
        )
        response = await client.get("/api/session-summaries/s_1")
    assert response.status_code == 200
