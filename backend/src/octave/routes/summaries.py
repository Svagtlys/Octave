"""Session-summary HTTP surface (issue #112).

The result-viewer backend: a list joining the caller's sessions to their
cached summaries, a peek-only detail, and a force-refresh that runs the
LLM. Routes own commits (deps compose ``get_db_session``); the
summarizer never commits. Owner-scoped join (spec Decision 4): a joined
non-owner sees "No summary yet" in the list even when the owner has one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from octave.auth.deps import get_current_user
from octave.db.adapter import DbAdapter
from octave.db.deps import get_db_session
from octave.db.errors import OwnerParticipantMissingError
from octave.db.models import User, VaultItem
from octave.db.session_store import SessionStore
from octave.db.types import VaultKind
from octave.db.vault_store import VaultStore

__all__ = ["router"]

router = APIRouter(prefix="/session-summaries", tags=["summaries"])

PREVIEW_MAX_CHARS = 140
"""Same route-boundary truncation as GET /api/sessions."""

_VAULT_PAGE = 200
"""Vault fetch page size; the summary join pages to exhaustion (a silent
cap would drop summaries — the ``injection._corpus`` rule)."""


def _db_adapter(request: Request) -> DbAdapter:
    adapter: DbAdapter | None = getattr(request.app.state, "db_adapter", None)
    if adapter is None:
        raise HTTPException(
            status_code=503, detail="database adapter not configured"
        )
    return adapter


class SummarySnippetResponse(BaseModel):
    title: str
    snippet: str
    model_name: str | None
    generated_at: datetime


class SummaryDetailResponse(BaseModel):
    title: str
    content: str
    model_name: str | None
    generated_at: datetime


class SessionSummaryListItem(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    last_activity: datetime
    last_message: str | None
    summary: SummarySnippetResponse | None


class SessionSummaryListResponse(BaseModel):
    items: list[SessionSummaryListItem]
    total: int
    limit: int
    offset: int


class SessionSummaryDetailResponse(BaseModel):
    session_id: str
    summary: SummaryDetailResponse | None


def _preview(content: str) -> str:
    if len(content) <= PREVIEW_MAX_CHARS:
        return content
    return content[: PREVIEW_MAX_CHARS - 1] + "…"


def _snippet(item: VaultItem) -> SummarySnippetResponse | None:
    """Lenient parse mirroring ``summaries._to_summary``: malformed meta is
    treated as absent; items whose meta.session_id is missing/mismatched
    are skipped."""
    meta = item.meta if isinstance(item.meta, dict) else {}
    session_id = meta.get("session_id")
    expected = item.id.removeprefix("session_summary:")
    if not isinstance(session_id, str) or session_id != expected:
        return None
    model_name = meta.get("model_name")
    return SummarySnippetResponse(
        title=item.name,
        snippet=_preview(item.content),
        model_name=model_name if isinstance(model_name, str) else None,
        generated_at=item.updated_at,
    )


async def _summaries_by_session(
    vault: VaultStore, user_id: str
) -> dict[str, SummarySnippetResponse]:
    """All the owner's summary items, paged to exhaustion, keyed by
    session_id (lenient parse; malformed entries skipped)."""
    out: dict[str, SummarySnippetResponse] = {}
    offset = 0
    while True:
        page = await vault.list_items(
            user_id=user_id,
            kind=VaultKind.SESSION_SUMMARY,
            limit=_VAULT_PAGE,
            offset=offset,
        )
        for item in page:
            snippet = _snippet(item)
            if snippet is not None:
                out[item.id.removeprefix("session_summary:")] = snippet
        if len(page) < _VAULT_PAGE:
            return out
        offset += _VAULT_PAGE


async def _require_participant_id(store: SessionStore, user: User) -> str:
    participant = await store.get_participant_for_user(user.id)
    if participant is None:
        raise OwnerParticipantMissingError(user.id)
    return participant.id


@router.get("")
async def list_session_summaries(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_db_session),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> SessionSummaryListResponse:
    """The caller's sessions (activity-ordered, paginated — same ordering
    as GET /api/sessions) each carrying its cached summary snippet or
    null. Summaries are owner-scoped: joined non-owner sessions show
    null even when the owner has one (spec Decision 4)."""
    store = SessionStore(session)
    participant_id = await _require_participant_id(store, user)
    rows, total = await store.list_sessions(
        participant_id=participant_id, limit=limit, offset=offset
    )
    summaries = await _summaries_by_session(
        VaultStore(_db_adapter(request), session), user.id
    )
    return SessionSummaryListResponse(
        items=[
            SessionSummaryListItem(
                id=row.id,
                title=row.title,
                created_at=row.created_at,
                last_activity=row.last_activity,
                last_message=_preview(row.last_message) if row.last_message else None,
                summary=summaries.get(row.id),
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
