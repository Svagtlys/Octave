"""WebSocket endpoint: authenticated echo/join hub.

Identity (issue #122): browsers attach same-origin cookies to the WS
upgrade, so the handshake resolves the ``octave_session`` cookie via the
same ``AuthService.resolve_session`` the HTTP deps use — no token in
query strings, nothing leaking into access logs (spec: WebSocket
identity). Failures close before ``accept()``: 4401 (no/invalid/expired
session), 4403 (join of a session the user neither owns nor
participates in).

**Known gap, recorded (spec):** identity is checked at handshake and per
``join``; an already-open socket survives account deactivation until it
reconnects. Accepted for 1.0 — periodic revalidation on heartbeat is a
noted follow-up, not an oversight.

The WS scope has no request-scoped dependency injection, so the handler
builds a transient ``AsyncSession`` from ``app.state.db_session_factory``
and owns its transaction (commit after the idle-expiry slide).
"""

import json
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, WebSocket
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.agent.composition import build_message_router
from octave.agent.router import StopReason
from octave.auth.config import AuthSettings
from octave.auth.deps import SESSION_COOKIE
from octave.auth.passwords import Argon2Hasher
from octave.auth.service import AuthService
from octave.auth.sliding import slide_session
from octave.auth.store import AuthStore
from octave.db.event_store import EventStore
from octave.db.models import Event, Participant, Session, SessionParticipant
from octave.db.models.base import utcnow
from octave.db.session_store import SessionStore
from octave.websocket.hub import SessionHub, get_hub

router = APIRouter()


def _service_factory(
    websocket: WebSocket,
) -> Callable[[AsyncSession], Awaitable[AuthService]]:
    """Transient-session AuthService factory; hasher/settings come from
    app.state exactly like octave.auth.deps (tests wire DummyHasher)."""

    async def _build(session: AsyncSession) -> AuthService:
        hasher = getattr(websocket.app.state, "auth_hasher", None) or Argon2Hasher()
        settings = (
            getattr(websocket.app.state, "auth_settings", None) or AuthSettings()
        )
        return AuthService(store=AuthStore(session), hasher=hasher, settings=settings)

    return _build


def _ws_settings(websocket: WebSocket) -> AuthSettings:
    return getattr(websocket.app.state, "auth_settings", None) or AuthSettings()


async def _resolve_identity(websocket: WebSocket) -> str | None:
    """Cookie -> user id (or None). Pure read (issue #161); the idle-expiry
    slide runs best-effort on its own transaction via ``slide_session``."""
    factory = getattr(websocket.app.state, "db_session_factory", None)
    if factory is None:
        return None
    token = websocket.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    async with factory() as session:
        service = await _service_factory(websocket)(session)
        resolved = await service.resolve_session(token)
    if resolved is None:
        return None
    await slide_session(factory, token, settings=_ws_settings(websocket), now=utcnow())
    return resolved[0].id


async def _may_join(user_id: str, session_id: str, websocket: WebSocket) -> bool:
    """Owner or current participant of the target session."""
    factory = websocket.app.state.db_session_factory
    async with factory() as db:
        owner = await db.scalar(
            select(Session.id).where(
                Session.id == session_id, Session.created_by_user_id == user_id
            )
        )
        if owner is not None:
            return True
        # Non-owner: must hold a live participant row (left_at NULL).
        participant = await db.scalar(
            select(SessionParticipant.session_id)
            .join(Participant, Participant.id == SessionParticipant.participant_id)
            .where(
                SessionParticipant.session_id == session_id,
                Participant.user_id == user_id,
                SessionParticipant.left_at.is_(None),
            )
        )
        return participant is not None


async def _max_seq(db: AsyncSession, session_id: str) -> int:
    row = await db.execute(
        select(func.coalesce(func.max(Event.seq), 0)).where(
            Event.session_id == session_id
        )
    )
    return int(row.scalar_one())


async def _labels(db: AsyncSession) -> dict[str, str]:
    rows = await db.execute(select(Participant.id, Participant.label))
    return {pid: label for pid, label in rows.all()}


class _HubStreamSink:
    """Per-frame ephemeral fan-out (issue #45): every delta broadcasts a
    stream_* frame to the session's joined sockets. Best-effort parity —
    SessionHub.broadcast evicts dead sockets itself, never fails the turn."""

    def __init__(self, hub: SessionHub, session_id: str) -> None:
        self._hub = hub
        self._session_id = session_id

    async def _send(self, payload: dict[str, object]) -> None:
        await self._hub.broadcast(self._session_id, payload)

    async def on_thinking_delta(self, *, round_index: int, text: str) -> None:
        await self._send(
            {
                "type": "stream_thinking",
                "session_id": self._session_id,
                "round": round_index,
                "delta": text,
            }
        )

    async def on_text_delta(self, *, round_index: int, text: str) -> None:
        await self._send(
            {
                "type": "stream_text",
                "session_id": self._session_id,
                "round": round_index,
                "delta": text,
            }
        )

    async def on_tool_call_start(
        self, *, round_index: int, index: int, call_id: str, name: str
    ) -> None:
        await self._send(
            {
                "type": "stream_tool_start",
                "session_id": self._session_id,
                "round": round_index,
                "index": index,
                "call_id": call_id,
                "name": name,
            }
        )

    async def on_tool_args_delta(
        self, *, round_index: int, index: int, arguments: str
    ) -> None:
        await self._send(
            {
                "type": "stream_tool_args",
                "session_id": self._session_id,
                "round": round_index,
                "index": index,
                "arguments": arguments,
            }
        )

    async def on_tool_result(
        self,
        *,
        round_index: int,
        call_id: str,
        name: str,
        content: str,
        is_error: bool,
    ) -> None:
        await self._send(
            {
                "type": "stream_tool_result",
                "session_id": self._session_id,
                "round": round_index,
                "call_id": call_id,
                "name": name,
                "content": content,
                "is_error": is_error,
            }
        )


async def _handle_user_message(
    websocket: WebSocket, user_id: str, session_id: str, content: str
) -> None:
    """One frame = one caller-owned transaction (WS scope convention):
    deliver, commit, then broadcast everything the turn appended."""
    app = websocket.app
    adapter = getattr(app.state, "inference_adapter", None)
    registry = getattr(app.state, "mcp_registry", None)
    if adapter is None or registry is None:
        await websocket.send_text(
            json.dumps(
                {"type": "error", "error": "request pipeline not configured"}
            )
        )
        return
    factory = app.state.db_session_factory
    hub = get_hub(app)
    async with factory() as db:
        participant = await SessionStore(db).get_participant_for_user(user_id)
        if participant is None:
            await websocket.send_text(
                json.dumps({"type": "error", "error": "no participant identity"})
            )
            return
        pre_max = await _max_seq(db, session_id)
        sink = _HubStreamSink(hub, session_id)
        router = build_message_router(
            db,
            adapter=adapter,
            tool_registry=registry,
            adapter_name=getattr(app.state, "inference_adapter_name", None),
            stream_sink=sink,
        )
        try:
            outcome = await router.deliver(
                session_id, author_participant_id=participant.id, content=content
            )
            await db.commit()
        except Exception as exc:  # noqa: BLE001 — frame failures never kill the socket
            await db.rollback()
            await websocket.send_text(json.dumps({"type": "error", "error": str(exc)}))
            return
        new_events = await EventStore(db).read(session_id, after_seq=pre_max)
        labels = await _labels(db)
    for event in new_events:
        await hub.broadcast(
            session_id,
            {
                "type": "event",
                "session_id": session_id,
                "seq": event.seq,
                "kind": event.kind,
                "author": (
                    labels.get(event.author_participant_id)
                    if event.author_participant_id
                    else None
                ),
                "content": event.payload.get("content"),
            },
        )
    if outcome.stop_reason == StopReason.ERROR:
        await websocket.send_text(
            json.dumps({"type": "error", "error": outcome.error or "turn failed"})
        )


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    user_id = await _resolve_identity(websocket)
    if user_id is None:
        # Close BEFORE accept: anonymous sockets never open.
        await websocket.close(code=4401)
        return
    await websocket.accept()
    hub = get_hub(websocket.app)
    joined: set[str] = set()
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            if message.get("type") == "join":
                session_id = message.get("session_id", "")
                if not await _may_join(user_id, session_id, websocket):
                    await websocket.close(code=4403)
                    return
                hub.join(session_id, websocket)
                joined.add(session_id)
                await websocket.send_text(
                    json.dumps({"type": "joined", "session_id": session_id})
                )
                continue
            if message.get("type") == "user_message":
                session_id = message.get("session_id", "")
                content = message.get("content", "")
                if not await _may_join(user_id, session_id, websocket):
                    await websocket.close(code=4403)
                    return
                await _handle_user_message(websocket, user_id, session_id, content)
                continue
            response = {
                "type": "pong",
                "payload": message.get("payload", ""),
            }
            await websocket.send_text(json.dumps(response))
    except Exception:
        await websocket.close()
    finally:
        for session_id in joined:
            hub.leave(session_id, websocket)
