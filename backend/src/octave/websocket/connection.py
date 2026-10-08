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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.auth.config import AuthSettings
from octave.auth.deps import SESSION_COOKIE
from octave.auth.passwords import Argon2Hasher
from octave.auth.service import AuthService
from octave.auth.store import AuthStore
from octave.db.models import Session, SessionParticipant

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


async def _resolve_identity(websocket: WebSocket) -> str | None:
    """Cookie -> user id (or None). Commits the idle-expiry slide: the WS
    scope owns its own transient session/transaction."""
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
        await session.commit()
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
        from octave.db.models import Participant

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


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    user_id = await _resolve_identity(websocket)
    if user_id is None:
        # Close BEFORE accept: anonymous sockets never open.
        await websocket.close(code=4401)
        return
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            if message.get("type") == "join":
                session_id = message.get("session_id", "")
                if not await _may_join(user_id, session_id, websocket):
                    await websocket.close(code=4403)
                    return
                await websocket.send_text(
                    json.dumps({"type": "joined", "session_id": session_id})
                )
                continue
            response = {
                "type": "pong",
                "payload": message.get("payload", ""),
            }
            await websocket.send_text(json.dumps(response))
    except Exception:
        await websocket.close()
