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
