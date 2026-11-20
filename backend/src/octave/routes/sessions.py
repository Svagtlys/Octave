"""Session lifecycle HTTP surface (issue #121).

POST creates a Session plus the owner's membership row in one transaction;
GET lists the caller's sessions, most-recently-active first, with a
last-message preview. Routes own commits (deps compose ``get_db_session``);
db-plane errors map to JSON once via ``octave.middleware`` (DbError -> 500).
The caller's missing Participant row fails loud — never self-healed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from octave.auth.deps import get_current_user
from octave.db.deps import get_db_session
from octave.db.errors import OwnerParticipantMissingError
from octave.db.models import User
from octave.db.session_store import SessionActivityRow, SessionStore

__all__ = ["router"]

router = APIRouter(prefix="/sessions", tags=["sessions"])

PREVIEW_MAX_CHARS = 140


class CreateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class SessionResponse(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    last_activity: datetime
    last_message: str | None


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    total: int
    limit: int
    offset: int


def _preview(content: str | None) -> str | None:
    """Truncate the preview at the route boundary (presentation concern; the
    store returns raw content). Total length stays <= PREVIEW_MAX_CHARS."""
    if content is None or len(content) <= PREVIEW_MAX_CHARS:
        return content
    return content[: PREVIEW_MAX_CHARS - 1] + "…"


async def _require_participant_id(
    store: SessionStore, user: User
) -> str:
    participant = await store.get_participant_for_user(user.id)
    if participant is None:
        raise OwnerParticipantMissingError(user.id)
    return participant.id


@router.post("", status_code=201)
async def create_session(
    body: CreateSessionRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_db_session),
) -> SessionResponse:
    """Create a session owned by the caller, with the caller's Participant
    as a speaker member. 201; fresh session: last_activity = created_at."""
    store = SessionStore(session)
    participant_id = await _require_participant_id(store, user)
    row = await store.create_session(
        owner_user_id=user.id,
        owner_participant_id=participant_id,
        title=body.title,
    )
    await session.commit()
    return SessionResponse(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        last_activity=row.created_at,
        last_message=None,
    )


def _to_response(row: SessionActivityRow) -> SessionResponse:
    return SessionResponse(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        last_activity=row.last_activity,
        last_message=_preview(row.last_message),
    )


@router.get("")
async def list_sessions(
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_db_session),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> SessionListResponse:
    """The caller's current memberships, most-recently-active first
    (transcript activity, fallback created_at), paginated."""
    store = SessionStore(session)
    participant_id = await _require_participant_id(store, user)
    rows, total = await store.list_sessions(
        participant_id=participant_id, limit=limit, offset=offset
    )
    return SessionListResponse(
        items=[_to_response(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
