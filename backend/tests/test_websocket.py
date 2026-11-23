"""WebSocket handshake auth (issue #122) + echo flow regression.

Identity rides the ``octave_session`` cookie the browser attaches to the
upgrade (no token in query strings — spec: nothing leaks into access
logs). Unknown/missing cookie -> close 4401 before accept; ``join`` on a
session the user neither owns nor participates in -> close 4403.

Seeding runs in the app's own lifespan so the sqlite engine and the
TestClient share one event loop.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from octave.auth.config import AuthSettings
from octave.auth.passwords import DummyHasher
from octave.auth.service import AuthService
from octave.auth.store import AuthStore
from octave.db._bootstrap import create_sqlite_engine
from octave.db.models import Base, Participant, Session, SessionParticipant
from octave.websocket.connection import router as ws_router


def _make_app(tmp_path: Path) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'ws.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False
        )
        app.state.db_session_factory = factory
        app.state.auth_hasher = DummyHasher()
        app.state.auth_settings = AuthSettings()

        async with factory() as s:
            store = AuthStore(s)
            alice = await store.create_user(
                username="alice", display_name="Alice", password_hash="dummy$pw"
            )
            bob = await store.create_user(
                username="bob", display_name="Bob", password_hash="dummy$pw"
            )
            # create_user already inserted each user's Participant row;
            # reuse alice's rather than inserting a second one.
            alice_pid = await s.scalar(
                select(Participant.id).where(Participant.user_id == alice.id)
            )
            s.add(Session(id="s_alice", created_by_user_id=alice.id, status="active"))
            s.add(SessionParticipant(session_id="s_alice", participant_id=alice_pid))
            s.add(Session(id="s_bob", created_by_user_id=bob.id, status="active"))
            await s.commit()
            service = AuthService(
                store=AuthStore(s), hasher=DummyHasher(), settings=AuthSettings()
            )
            app.state.tokens = {
                "alice": await service.login("alice", "pw"),
                "bob": await service.login("bob", "pw"),
            }
            await s.commit()
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(lifespan=lifespan)
    app.include_router(ws_router)
    return app


@pytest.fixture
def ws_client(tmp_path: Path):
    with TestClient(_make_app(tmp_path)) as client:
        yield client


def _cookie(token: str) -> dict[str, str]:
    return {"cookie": f"octave_session={token}"}


def test_handshake_without_cookie_closes_4401(ws_client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect("/ws"):
            pass
    assert exc.value.code == 4401


def test_handshake_with_garbage_cookie_closes_4401(ws_client: TestClient) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_client.websocket_connect("/ws", headers=_cookie("not-a-token")):
            pass
    assert exc.value.code == 4401


def test_echo_with_valid_cookie(ws_client: TestClient) -> None:
    token = ws_client.app.state.tokens["alice"]
    with ws_client.websocket_connect("/ws", headers=_cookie(token)) as ws:
        ws.send_text(json.dumps({"type": "ping", "payload": "hello"}))
        response = json.loads(ws.receive_text())
    assert response["type"] == "pong"
    assert response["payload"] == "hello"


def test_join_own_session_ok(ws_client: TestClient) -> None:
    token = ws_client.app.state.tokens["alice"]
    with ws_client.websocket_connect("/ws", headers=_cookie(token)) as ws:
        ws.send_text(json.dumps({"type": "join", "session_id": "s_alice"}))
        response = json.loads(ws.receive_text())
    assert response == {"type": "joined", "session_id": "s_alice"}


def test_join_foreign_session_closes_4403(ws_client: TestClient) -> None:
    token = ws_client.app.state.tokens["bob"]
    with ws_client.websocket_connect("/ws", headers=_cookie(token)) as ws:
        ws.send_text(json.dumps({"type": "join", "session_id": "s_alice"}))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4403


# --- user_message flow (issue #60) -------------------------------------

from octave.agent import AgentInstanceManager
from octave.agent.composition import build_message_router  # noqa: F401  (documented surface)
from octave.db.models import Agent
from octave.inference.types import Message  # noqa: F401
from tests.agent.fakes import ScriptedAdapter, final_result, tool_call, tool_result
from tests.agent.test_turn_runner import FakeRegistry

_FAKE_BINDING = {"kind": "explicit", "adapter": "fake", "model": "mock-model"}


def _make_pipeline_app(tmp_path: Path) -> FastAPI:
    """App with the full request-flow seams: scripted adapter + fake MCP
    registry on app.state, agent spawned into s_alice."""
    app = _make_app(tmp_path)
    # Capture the original lifespan BEFORE reassignment: reading
    # app.router.lifespan_context inside the new one would resolve to the
    # new function itself (infinite recursion).
    base = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(inner_app: FastAPI) -> AsyncIterator[None]:
        async with base(inner_app):
            async with inner_app.state.db_session_factory() as s:
                s.add(Agent(id="a_oct", name="Octave", model_binding=_FAKE_BINDING))
                await s.commit()
            async with inner_app.state.db_session_factory() as s:
                await AgentInstanceManager(s).spawn(
                    agent_id="a_oct", session_id="s_alice"
                )
                await s.commit()
            inner_app.state.inference_adapter = ScriptedAdapter(
                [
                    tool_result(tool_call("c1", "mcp__fs__write", '{"a": 1}')),
                    final_result("File written."),
                ]
            )
            inner_app.state.inference_adapter_name = "fake"
            inner_app.state.mcp_registry = FakeRegistry()
            yield

    app.router.lifespan_context = lifespan
    return app


@pytest.fixture
def pipeline_client(tmp_path: Path):
    with TestClient(_make_pipeline_app(tmp_path)) as client:
        yield client


def test_user_message_drives_full_flow_and_broadcasts(
    pipeline_client: TestClient,
) -> None:
    token = pipeline_client.app.state.tokens["alice"]
    with pipeline_client.websocket_connect("/ws", headers=_cookie(token)) as ws:
        ws.send_text(json.dumps({"type": "join", "session_id": "s_alice"}))
        assert json.loads(ws.receive_text())["type"] == "joined"
        ws.send_text(
            json.dumps(
                {"type": "user_message", "session_id": "s_alice", "content": "hi"}
            )
        )
        frames = [json.loads(ws.receive_text()) for _ in range(5)]
    assert [f["kind"] for f in frames] == [
        "user_message",
        "context_injection",
        "tool_call",
        "tool_result",
        "assistant_message",
    ]
    assert [f["seq"] for f in frames] == [1, 2, 3, 4, 5]
    assert frames[0]["content"] == "hi"
    assert frames[0]["author"] == "Alice"
    assert frames[4]["content"] == "File written."
    assert frames[4]["author"] == "Octave"


def test_user_message_without_adapter_sends_error_frame(
    ws_client: TestClient,
) -> None:
    """ws_client app has no inference_adapter on state -> error frame,
    socket stays open."""
    token = ws_client.app.state.tokens["alice"]
    with ws_client.websocket_connect("/ws", headers=_cookie(token)) as ws:
        ws.send_text(json.dumps({"type": "join", "session_id": "s_alice"}))
        json.loads(ws.receive_text())  # joined
        ws.send_text(
            json.dumps(
                {"type": "user_message", "session_id": "s_alice", "content": "hi"}
            )
        )
        frame = json.loads(ws.receive_text())
        assert frame["type"] == "error"
        assert "not configured" in frame["error"]
        ws.send_text(json.dumps({"type": "ping", "payload": "still alive"}))
        assert json.loads(ws.receive_text())["type"] == "pong"


def test_user_message_foreign_session_closes_4403(
    pipeline_client: TestClient,
) -> None:
    token = pipeline_client.app.state.tokens["bob"]
    with pipeline_client.websocket_connect("/ws", headers=_cookie(token)) as ws:
        ws.send_text(
            json.dumps(
                {"type": "user_message", "session_id": "s_alice", "content": "hi"}
            )
        )
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4403
