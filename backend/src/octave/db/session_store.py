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
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.db.models import Event, Participant, Session, SessionParticipant
from octave.db.types import EventKind

__all__ = ["SessionActivityRow", "SessionStore"]

_MESSAGE_KINDS = (str(EventKind.USER_MESSAGE), str(EventKind.ASSISTANT_MESSAGE))


@dataclass(frozen=True)
class SessionActivityRow:
    """One listed session with transcript-derived activity (raw, untruncated)."""

    id: str
    title: str | None
    created_at: datetime
    last_activity: datetime
    last_message: str | None


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
