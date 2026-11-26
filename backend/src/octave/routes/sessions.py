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

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from octave.auth.deps import get_current_user
from octave.db.deps import get_db_session
from octave.db.errors import OwnerParticipantMissingError
from octave.db.models import User
from octave.db.session_store import (
    SessionActivityRow,
    SessionStore,
    TranscriptMessageRow,
)

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


class TranscriptMessageResponse(BaseModel):
    seq: int
    kind: str
    author: str | None
    content: str | None
    timestamp: datetime
    thinking: str | None = None
    """Dialect reasoning content on assistant rows (issue #45)."""


class TranscriptListResponse(BaseModel):
    items: list[TranscriptMessageResponse]
    next_cursor: int | None
    prev_cursor: int | None = None
    """Backward-paging cursor (issue #43): echo the oldest ``seq`` returned
    to fetch the page before it; ``null`` when no older messages exist.
    Only set in ``before_seq``/``end`` mode."""


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


@router.get("/{session_id}/messages")
async def list_session_messages(
    session_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_db_session),
    cursor: int | None = Query(None, ge=0),
    before_seq: int | None = Query(None, ge=1),
    end: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
) -> TranscriptListResponse:
    """Session transcript page: ``user_message``/``assistant_message``
    events, cursor-paginated by ``seq`` (#99 convention). Default and
    ``cursor`` mode: forward, oldest-first, ``next_cursor`` terminates.
    ``end=true``: the newest page (chat-open, issue #43); ``before_seq=N``:
    the page just below ``seq`` N — both render oldest-first within the
    page and carry ``prev_cursor`` (null when no older messages remain).
    Combining modes → 422. Access: owner or current participant —
    404 unknown session, 403 forbidden (mirrors the WS join guard)."""
    modes = [x is not None for x in (cursor, before_seq)] + [end]
    if sum(modes) > 1:
        raise HTTPException(
            status_code=422, detail="cursor, before_seq and end are mutually exclusive"
        )
    store = SessionStore(session)
    verdict = await store.resolve_access(session_id, user.id)
    if verdict == "missing":
        raise HTTPException(status_code=404, detail="Session not found")
    if verdict == "denied":
        raise HTTPException(status_code=403, detail="Not a member of this session")

    if end or before_seq is not None:
        rows = await store.list_message_page_before(
            session_id, before_seq=before_seq, limit=limit + 1
        )
        has_older = len(rows) > limit
        page = sorted(rows[:limit], key=lambda row: row.seq)
        items = [_to_message_response(row) for row in page]
        prev_cursor = page[0].seq if has_older and page else None
        return TranscriptListResponse(
            items=items, next_cursor=None, prev_cursor=prev_cursor
        )

    rows = await store.list_message_page(
        session_id, after_seq=cursor or 0, limit=limit + 1
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    items = [_to_message_response(row) for row in page]
    next_cursor = page[-1].seq if has_more else None
    return TranscriptListResponse(items=items, next_cursor=next_cursor)


def _to_message_response(row: TranscriptMessageRow) -> TranscriptMessageResponse:
    return TranscriptMessageResponse(
        seq=row.seq,
        kind=row.kind,
        author=row.author_label,
        content=row.content,
        timestamp=row.created_at,
        thinking=row.thinking,
    )


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Hard delete an owned session (issue #160). Owner-only: a live
    participant may read but never destroy the transcript. 404 unknown id
    (repeat delete included), 403 exists-but-not-owner, 204 on success with
    an empty body. Children (participants/events/agent_instances/child
    sessions) go via schema-level FK CASCADE; the route owns the commit."""
    store = SessionStore(session)
    owner = await store.get_session_owner(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if owner != user.id:
        raise HTTPException(
            status_code=403, detail="Only the owner can delete this session"
        )
    await store.delete_session(session_id)
    await session.commit()
    return Response(status_code=204)
