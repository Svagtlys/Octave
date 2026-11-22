"""Session routes over the real app surface (issue #121).

App assembled like tests/routes/test_auth_routes.py: temp-file SQLite,
DummyHasher, auth + sessions routers, add_error_handlers. Events seeded via
EventStore, then re-timed for deterministic ordering.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.auth.config import AuthSettings
from octave.auth.deps import SESSION_COOKIE
from octave.auth.passwords import DummyHasher
from octave.db._bootstrap import create_sqlite_engine
from octave.db.event_store import EventStore
from octave.db.models import Base, Participant, Session, SessionParticipant
from octave.db.types import EventKind
from octave.middleware import add_error_handlers
from octave.routes.auth import router as auth_router
from octave.routes.sessions import router as sessions_router


def _dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


async def _sessions_app(
    tmp_path: Path,
) -> tuple[FastAPI, async_sessionmaker[AsyncSession]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'routes.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.state.db_session_factory = factory
    app.state.auth_hasher = DummyHasher()
    app.state.auth_settings = AuthSettings(cookie_secure=False)
    add_error_handlers(app)
    app.include_router(auth_router, prefix="/api")
    app.include_router(sessions_router, prefix="/api")
    return app, factory


@pytest.fixture
async def app_and_factory(tmp_path: Path):
    return await _sessions_app(tmp_path)


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _bootstrap(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/bootstrap",
        json={"username": "alice", "password": "pw", "display_name": "Alice"},
    )
    assert response.status_code == 201


async def _participant_id(factory, user_id: str) -> str:
    async with factory() as session:
        return (
            await session.execute(
                select(Participant.id).where(Participant.user_id == user_id)
            )
        ).scalar_one()


async def _seed_activity(
    factory,
    session_id: str,
    events: list[tuple[EventKind, str, datetime]],
    *,
    author: str | None = None,
) -> None:
    async with factory() as session:
        for kind, content, when in events:
            event = await EventStore(session).append(
                session_id,
                kind,
                author_participant_id=author,
                payload={"content": content},
            )
            event.created_at = when
        await session.commit()


async def _set_session_created_at(
    factory, session_id: str, when: datetime
) -> None:
    async with factory() as session:
        row = await session.get(Session, session_id)
        assert row is not None
        row.created_at = when
        await session.commit()


# -- create ------------------------------------------------------------------


async def test_create_requires_auth(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        assert (await client.post("/api/sessions", json={})).status_code == 401
        assert (await client.get("/api/sessions")).status_code == 401


async def test_create_returns_201_and_persists(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        me = (await client.get("/api/auth/me")).json()
        response = await client.post("/api/sessions", json={"title": "Groceries"})
        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "Groceries"
        assert body["last_message"] is None
        assert body["last_activity"] == body["created_at"]

    async with factory() as session:
        srow = await session.get(Session, body["id"])
        assert srow is not None
        assert srow.status == "active"
        assert srow.created_by_user_id == me["id"]
        membership = (
            await session.execute(select(SessionParticipant))
        ).scalars().one()
        assert membership.participant_id == await _participant_id(
            factory, me["id"]
        )
        assert membership.role == "speaker"
        assert membership.left_at is None


async def test_create_title_optional(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        response = await client.post("/api/sessions", json={})
        assert response.status_code == 201
        assert response.json()["title"] is None


async def test_create_rejects_oversized_title(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        response = await client.post(
            "/api/sessions", json={"title": "x" * 201}
        )
        assert response.status_code == 422


async def test_create_fails_loud_without_participant(app_and_factory) -> None:
    """Missing owner Participant is a data-integrity bug: 500, never a
    self-healed insert (design Decision 5)."""
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        me = (await client.get("/api/auth/me")).json()
    async with factory() as session:
        await session.execute(
            text("DELETE FROM participants WHERE user_id = :uid"),
            {"uid": me["id"]},
        )
        await session.commit()
    async with await _client(app) as client:
        await _bootstrap_cookie(client, me)
        response = await client.post("/api/sessions", json={})
        assert response.status_code == 500
        assert response.json()["error"] == "OwnerParticipantMissingError"


async def _bootstrap_cookie(client: AsyncClient, identity: dict) -> None:
    """Re-login the (still-existing) user after the participant row was
    deleted, so get_current_user resolves but the lookup fails."""
    response = await client.post(
        "/api/auth/login",
        json={"username": identity["username"], "password": "pw"},
    )
    assert response.status_code == 200
    assert SESSION_COOKIE in response.cookies


# -- list ---------------------------------------------------------------------


async def test_list_scoped_to_caller(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as owner:
        await _bootstrap(owner)  # alice, owner role
        created = await owner.post(
            "/api/auth/users",
            json={
                "username": "bob",
                "password": "pw",
                "display_name": "Bob",
            },
        )
        assert created.status_code == 201
        await owner.post("/api/sessions", json={"title": "Alice only"})

        bob = await _client(app)
        await bob.post(
            "/api/auth/login", json={"username": "bob", "password": "pw"}
        )
        bob_list = await bob.get("/api/sessions")
        assert bob_list.status_code == 200
        assert bob_list.json()["items"] == []
        assert bob_list.json()["total"] == 0

        alice_list = await owner.get("/api/sessions")
        assert [item["title"] for item in alice_list.json()["items"]] == [
            "Alice only"
        ]
        assert alice_list.json()["total"] == 1


async def test_list_orders_most_recently_active_first(app_and_factory) -> None:
    """Event activity beats creation time; eventless sessions fall back to
    created_at."""
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        ids = {}
        for sid, day in [("s_a", 1), ("s_b", 2), ("s_c", 3)]:
            response = await client.post("/api/sessions", json={"title": sid})
            ids[sid] = response.json()["id"]
    for sid, day in [("s_a", 1), ("s_b", 2), ("s_c", 3)]:
        await _set_session_created_at(factory, ids[sid], _dt(day))
    await _seed_activity(
        factory, ids["s_a"], [(EventKind.USER_MESSAGE, "hi", _dt(10))]
    )
    async with await _client(app) as client:
        await _bootstrap_login(client)
        body = (await client.get("/api/sessions")).json()
        assert [item["id"] for item in body["items"]] == [
            ids["s_a"],
            ids["s_c"],
            ids["s_b"],
        ]


async def _bootstrap_login(client: AsyncClient) -> None:
    """Re-authenticate an existing client on a fresh app (alice persists in
    the temp DB)."""
    response = await client.post(
        "/api/auth/login", json={"username": "alice", "password": "pw"}
    )
    assert response.status_code == 200


async def test_preview_truncated_at_140(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        session_id = (await client.post("/api/sessions", json={})).json()["id"]
        alice = await _participant_id(
            factory, (await client.get("/api/auth/me")).json()["id"]
        )
    await _seed_activity(
        factory,
        session_id,
        [(EventKind.USER_MESSAGE, "x" * 200, _dt(5))],
        author=alice,
    )
    async with await _client(app) as client:
        await _bootstrap_login(client)
        item = (await client.get("/api/sessions")).json()["items"][0]
        assert item["last_message"] == "x" * 139 + "…"
        assert len(item["last_message"]) == 140


async def test_preview_null_without_message_events(app_and_factory) -> None:
    """tool_call counts for activity but never produces a preview."""
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        session_id = (await client.post("/api/sessions", json={})).json()["id"]
    await _seed_activity(
        factory, session_id, [(EventKind.TOOL_CALL, "tool", _dt(7))]
    )
    async with await _client(app) as client:
        await _bootstrap_login(client)
        item = (await client.get("/api/sessions")).json()["items"][0]
        assert item["last_message"] is None
        assert datetime.fromisoformat(item["last_activity"]) == _dt(7)


async def test_list_pagination_envelope(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        ids = {}
        for sid, day in [("s_1", 1), ("s_2", 2), ("s_3", 3)]:
            response = await client.post("/api/sessions", json={"title": sid})
            ids[sid] = response.json()["id"]
    for sid, day in [("s_1", 1), ("s_2", 2), ("s_3", 3)]:
        await _set_session_created_at(factory, ids[sid], _dt(day))
    async with await _client(app) as client:
        await _bootstrap_login(client)
        defaults = (await client.get("/api/sessions")).json()
        assert defaults["limit"] == 20
        assert defaults["offset"] == 0
        assert defaults["total"] == 3

        page = (await client.get("/api/sessions?limit=2&offset=0")).json()
        assert [item["id"] for item in page["items"]] == [
            ids["s_3"],
            ids["s_2"],
        ]
        assert page["total"] == 3

        beyond = (await client.get("/api/sessions?limit=2&offset=5")).json()
        assert beyond["items"] == []
        assert beyond["total"] == 3


async def test_pagination_validation(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        for url in (
            "/api/sessions?limit=0",
            "/api/sessions?limit=101",
            "/api/sessions?offset=-1",
        ):
            assert (await client.get(url)).status_code == 422, url


async def _login(client: AsyncClient, username: str) -> None:
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": "pw"}
    )
    assert response.status_code == 200


async def _create_member(owner: AsyncClient, name: str) -> str:
    """Owner (bootstrapped alice) creates a member account; returns the
    member's user id."""
    created = await owner.post(
        "/api/auth/users",
        json={"username": name, "password": "pw", "display_name": name.title()},
    )
    assert created.status_code == 201
    return created.json()["id"]


# -- GET /sessions/{id}/messages (issue #151) ------------------------------------


async def test_messages_requires_auth(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        response = await client.get("/api/sessions/whatever/messages")
        assert response.status_code == 401


async def test_messages_unknown_session_404(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        response = await client.get("/api/sessions/nope/messages")
        assert response.status_code == 404


async def test_messages_non_member_403(app_and_factory) -> None:
    """A session that exists but the caller neither owns nor joined."""
    app, factory = app_and_factory
    async with await _client(app) as owner:
        await _bootstrap(owner)  # alice
        await _create_member(owner, "bob")
        session_id = (await owner.post("/api/sessions", json={})).json()["id"]
        bob = await _client(app)
        await _login(bob, "bob")
        response = await bob.get(f"/api/sessions/{session_id}/messages")
        assert response.status_code == 403


async def test_messages_joined_non_owner_200(app_and_factory) -> None:
    """Membership (not ownership) grants reads — the WS-join axis."""
    app, factory = app_and_factory
    async with await _client(app) as owner:
        await _bootstrap(owner)
        bob_id = await _create_member(owner, "bob")
        session_id = (await owner.post("/api/sessions", json={})).json()["id"]
        bob_pid = await _participant_id(factory, bob_id)
    async with factory() as db:
        db.add(SessionParticipant(session_id=session_id, participant_id=bob_pid))
        await db.commit()
    async with await _client(app) as bob:
        await _login(bob, "bob")
        response = await bob.get(f"/api/sessions/{session_id}/messages")
        assert response.status_code == 200


async def test_messages_returns_transcript_with_labels(app_and_factory) -> None:
    """Owner reads their transcript: ordered items, author labels,
    non-message kinds excluded, timestamps present."""
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        me = (await client.get("/api/auth/me")).json()
        session_id = (await client.post("/api/sessions", json={})).json()["id"]
        alice = await _participant_id(factory, me["id"])
    await _seed_activity(
        factory,
        session_id,
        [
            (EventKind.USER_MESSAGE, "hi", _dt(5)),
            (EventKind.TOOL_CALL, "tool", _dt(6)),
            (EventKind.ASSISTANT_MESSAGE, "hello", _dt(7)),
        ],
        author=alice,
    )
    async with await _client(app) as client:
        await _bootstrap_login(client)
        body = (await client.get(f"/api/sessions/{session_id}/messages")).json()
    assert [item["kind"] for item in body["items"]] == [
        "user_message",
        "assistant_message",
    ]
    assert [item["seq"] for item in body["items"]] == [1, 3]
    assert [item["content"] for item in body["items"]] == ["hi", "hello"]
    assert [item["author"] for item in body["items"]] == ["Alice", "Alice"]
    assert body["items"][0]["timestamp"].startswith("2026-01-05")
    assert body["next_cursor"] is None


async def test_messages_empty_session(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        session_id = (await client.post("/api/sessions", json={})).json()["id"]
        body = (await client.get(f"/api/sessions/{session_id}/messages")).json()
        assert body == {"items": [], "next_cursor": None}


async def test_messages_pagination_walk(app_and_factory) -> None:
    """limit=2 over 3 messages: page 1 carries next_cursor = last seq; the
    walk ends with next_cursor null."""
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        me = (await client.get("/api/auth/me")).json()
        session_id = (await client.post("/api/sessions", json={})).json()["id"]
        alice = await _participant_id(factory, me["id"])
    await _seed_activity(
        factory,
        session_id,
        [
            (EventKind.USER_MESSAGE, "m1", _dt(1)),
            (EventKind.ASSISTANT_MESSAGE, "m2", _dt(2)),
            (EventKind.USER_MESSAGE, "m3", _dt(3)),
        ],
        author=alice,
    )
    async with await _client(app) as client:
        await _bootstrap_login(client)
        page1 = (
            await client.get(f"/api/sessions/{session_id}/messages?limit=2")
        ).json()
        assert [i["seq"] for i in page1["items"]] == [1, 2]
        assert page1["next_cursor"] == 2

        page2 = (
            await client.get(
                f"/api/sessions/{session_id}/messages?limit=2&cursor=2"
            )
        ).json()
        assert [i["seq"] for i in page2["items"]] == [3]
        assert page2["next_cursor"] is None


async def test_messages_pagination_validation(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        session_id = (await client.post("/api/sessions", json={})).json()["id"]
        for url in (
            f"/api/sessions/{session_id}/messages?limit=0",
            f"/api/sessions/{session_id}/messages?limit=201",
            f"/api/sessions/{session_id}/messages?cursor=-1",
        ):
            assert (await client.get(url)).status_code == 422, url
