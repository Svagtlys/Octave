"""Session lifecycle queries: create + membership-scoped activity listing.

Plane-owned store over one caller-supplied ``AsyncSession`` — same contract as
``EventStore``/``VaultStore``/``McpConfigStore``: **never commits**; routes own
transaction boundaries (``octave.db.deps.get_db_session``).

Activity and preview are transcript-derived (2026-11-20 design spec):
``last_activity`` is ``COALESCE(MAX(events.created_at), sessions.created_at)``
and ``last_message`` the content of the latest ``user_message`` /
``assistant_message`` event (other kinds never produce a preview). Correlated
scalar subqueries keep ``ORDER BY``/``LIMIT``/``OFFSET`` a single statement
without ``GROUP BY``; the preview subquery rides the unique index
``(session_id, seq)``.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from octave.db.models import Event, Participant, Session, SessionParticipant
from octave.db.types import EventKind

__all__ = [
    "AccessVerdict",
    "SessionActivityRow",
    "SessionStore",
    "TranscriptMessageRow",
]

_MESSAGE_KINDS = (str(EventKind.USER_MESSAGE), str(EventKind.ASSISTANT_MESSAGE))

AccessVerdict = Literal["missing", "denied", "granted"]
"""Session-read access outcome (issue #151). `granted` = owner or current
participant (mirrors the WS join guard); `denied` = session exists but the
caller holds neither; `missing` = no such session. The route maps verdicts
to HTTP (404/403); the store stays HTTP-agnostic."""


@dataclass(frozen=True)
class SessionActivityRow:
    """One listed session with transcript-derived activity (raw, untruncated)."""

    id: str
    title: str | None
    created_at: datetime
    last_activity: datetime
    last_message: str | None


@dataclass(frozen=True)
class TranscriptMessageRow:
    """One transcript message, raw (store-level): label and content
    unprocessed. ``author_label`` is NULL when the author row is absent
    (schema-nullable FK); ``content`` is NULL on a corrupt payload (the
    preview-subquery tolerance)."""

    seq: int
    kind: str
    author_label: str | None
    content: str | None
    created_at: datetime
    thinking: str | None = None
    """Dialect reasoning content on assistant rows (issue #45); None elsewhere."""


def _to_transcript_row(row: Any) -> TranscriptMessageRow:
    """Map a joined ``(seq, kind, label, payload, created_at)`` row; corrupt
    payloads degrade to ``content=None`` (preview-subquery tolerance)."""
    raw: Any = row.payload
    content = raw.get("content") if isinstance(raw, dict) else None
    thinking = raw.get("thinking") if isinstance(raw, dict) else None
    return TranscriptMessageRow(
        seq=row.seq,
        kind=row.kind,
        author_label=row.label,
        content=content,
        created_at=row.created_at,
        thinking=thinking,
    )


class SessionStore:
    """Session create/list queries. Never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_participant_for_user(self, user_id: str) -> Participant | None:
        """The user's speaker identity. ``None`` means the create_user
        invariant is broken — callers fail loud, never self-heal."""
        result = await self._session.execute(
            select(Participant).where(Participant.user_id == user_id)
        )
        return result.scalars().first()

    async def create_session(
        self, *, owner_user_id: str, owner_participant_id: str, title: str | None
    ) -> Session:
        """Insert the Session (status ``active``, uuid4-hex id) and the owner's
        membership row (role ``speaker``) in one flush. Never commits."""
        row = Session(
            id=uuid.uuid4().hex,
            created_by_user_id=owner_user_id,
            status="active",
            title=title,
        )
        self._session.add(row)
        self._session.add(
            SessionParticipant(
                session_id=row.id,
                participant_id=owner_participant_id,
                role="speaker",
            )
        )
        await self._session.flush()
        return row

    async def list_sessions(
        self, *, participant_id: str, limit: int, offset: int
    ) -> tuple[list[SessionActivityRow], int]:
        """Current memberships for ``participant_id``, most-recently-active
        first (``COALESCE(MAX(events.created_at), created_at)``, tie-break id
        DESC), plus the caller's total membership count. Never commits."""
        last_activity = func.coalesce(
            select(func.max(Event.created_at))
            .where(Event.session_id == Session.id)
            .correlate(Session)
            .scalar_subquery(),
            Session.created_at,
        ).label("last_activity")
        last_message_payload = (
            select(Event.payload)
            .where(
                Event.session_id == Session.id,
                Event.kind.in_(_MESSAGE_KINDS),
            )
            .order_by(Event.seq.desc())
            .limit(1)
            .correlate(Session)
            .scalar_subquery()
            .label("last_message_payload")
        )
        membership = select(SessionParticipant.session_id).where(
            SessionParticipant.participant_id == participant_id,
            SessionParticipant.left_at.is_(None),
        )
        rows = await self._session.execute(
            select(
                Session.id,
                Session.title,
                Session.created_at,
                last_activity,
                last_message_payload,
            )
            .where(Session.id.in_(membership))
            .order_by(last_activity.desc(), Session.id.desc())
            .limit(limit)
            .offset(offset)
        )
        total = await self._session.scalar(
            select(func.count())
            .select_from(SessionParticipant)
            .where(
                SessionParticipant.participant_id == participant_id,
                SessionParticipant.left_at.is_(None),
            )
        )
        items: list[SessionActivityRow] = []
        for row in rows:
            raw: Any = row.last_message_payload
            content = raw.get("content") if isinstance(raw, dict) else None
            items.append(
                SessionActivityRow(
                    id=row.id,
                    title=row.title,
                    created_at=row.created_at,
                    last_activity=row.last_activity,
                    last_message=content,
                )
            )
        return items, int(total or 0)

    async def resolve_access(self, session_id: str, user_id: str) -> AccessVerdict:
        """Owner (``created_by_user_id``) or live participant (``left_at IS
        NULL``) -> ``granted``; session row absent -> ``missing``; else
        ``denied``. Mirrors the WS join guard (``_may_join``)."""
        owner = await self._session.scalar(
            select(Session.id).where(
                Session.id == session_id, Session.created_by_user_id == user_id
            )
        )
        if owner is not None:
            return "granted"
        participant = await self._session.scalar(
            select(SessionParticipant.session_id)
            .join(Participant, Participant.id == SessionParticipant.participant_id)
            .where(
                SessionParticipant.session_id == session_id,
                Participant.user_id == user_id,
                SessionParticipant.left_at.is_(None),
            )
        )
        if participant is not None:
            return "granted"
        exists = await self._session.scalar(
            select(Session.id).where(Session.id == session_id)
        )
        return "missing" if exists is None else "denied"

    async def get_session_owner(self, session_id: str) -> str | None:
        """``created_by_user_id`` for the row, or ``None`` when absent (issue
        #160). The DELETE guard's read half: the route compares the owner to
        the caller (owner-only delete; participant reads stay resolve_access).
        Never commits."""
        owner: str | None = await self._session.scalar(
            select(Session.created_by_user_id).where(Session.id == session_id)
        )
        return owner

    async def delete_session(self, session_id: str) -> int:
        """Hard delete of the sessions row; returns rowcount (issue #160).
        DB-level FK CASCADE (PRAGMA foreign_keys=ON) removes
        session_participants, events, agent_instances and child sessions —
        no app-side child deletes. Bulk statement: no ORM object load.
        Never commits."""
        result = await self._session.execute(
            delete(Session).where(Session.id == session_id)
        )
        return int(cast(CursorResult[Any], result).rowcount)

    async def list_message_page(
        self, session_id: str, *, after_seq: int, limit: int
    ) -> list[TranscriptMessageRow]:
        """Transcript window ``seq > after_seq`` (message kinds only), seq
        ASC, ``LIMIT limit`` — the caller over-fetches by one for a has-more
        probe. LEFT JOIN participants for the author label. Never commits."""
        rows = await self._session.execute(
            select(
                Event.seq,
                Event.kind,
                Participant.label,
                Event.payload,
                Event.created_at,
            )
            .outerjoin(Participant, Participant.id == Event.author_participant_id)
            .where(
                Event.session_id == session_id,
                Event.kind.in_(_MESSAGE_KINDS),
                Event.seq > after_seq,
            )
            .order_by(Event.seq)
            .limit(limit)
        )
        return [_to_transcript_row(row) for row in rows]

    async def list_message_page_before(
        self, session_id: str, *, before_seq: int | None, limit: int
    ) -> list[TranscriptMessageRow]:
        """Transcript window ``seq < before_seq`` (message kinds only;
        ``None`` = unbounded), seq DESC, ``LIMIT limit`` — the newest-side
        page for chat-open UX (issue #43). Rows come back DESC (newest
        first); the route sorts for display. Over-fetch by one for a
        has-more-older probe. LEFT JOIN participants for the label.
        Never commits."""
        query = (
            select(
                Event.seq,
                Event.kind,
                Participant.label,
                Event.payload,
                Event.created_at,
            )
            .outerjoin(Participant, Participant.id == Event.author_participant_id)
            .where(
                Event.session_id == session_id,
                Event.kind.in_(_MESSAGE_KINDS),
            )
            .order_by(Event.seq.desc())
            .limit(limit)
        )
        if before_seq is not None:
            query = query.where(Event.seq < before_seq)
        rows = await self._session.execute(query)
        return [_to_transcript_row(row) for row in rows]
