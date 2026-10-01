"""Transcript write path: append/read events with gap-free seq (issue #27).

``events.seq`` is documented as per-session monotonic (gap-free); clock
resolution cannot guarantee this, so seq assignment has exactly one owner.
Follows the VaultStore convention — constructed with the caller's
AsyncSession, never commits; callers own transaction boundaries
(``octave.db.deps``). The uq_events_session_seq constraint is the backstop
for the seq race: the insert runs in a SAVEPOINT so a collision is
recoverable without poisoning the caller's transaction.

Payload validation: ``user_message``/``assistant_message`` validate against
their ``octave.db.types`` models; ``tool_call``/``tool_result``/``system``
pass through as dicts (validation assigned to consumers, per the types
module docstring).
"""

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from octave.db.models import Event
from octave.db.types import (
    AssistantMessagePayload,
    ContextInjectionPayload,
    EventKind,
    UserMessagePayload,
)

__all__ = ["EventStore"]

_PAYLOAD_MODELS: dict[EventKind, type[BaseModel]] = {
    EventKind.USER_MESSAGE: UserMessagePayload,
    EventKind.ASSISTANT_MESSAGE: AssistantMessagePayload,
    EventKind.CONTEXT_INJECTION: ContextInjectionPayload,
}

_RETRY_LIMIT = 2


class EventStore:
    """The canonical event append/read surface. Never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        session_id: str,
        kind: EventKind,
        *,
        author_participant_id: str | None = None,
        target_participant_id: str | None = None,
        payload: dict[str, Any],
    ) -> Event:
        """Append one event, assigning the next gap-free ``seq``.

        Malformed payloads for validated kinds raise ``ValidationError``
        (a write-path bug — fail loud). On a ``seq`` collision the insert
        retries with a freshly computed seq up to ``_RETRY_LIMIT`` times,
        then re-raises. Cross-connection effectiveness is engine-dependent
        (SQLite snapshots stale reads within a transaction); the constraint
        guarantees no duplicate ever lands either way.
        """
        validator = _PAYLOAD_MODELS.get(kind)
        if validator is not None:
            payload = validator.model_validate(payload).model_dump()
        last_error: IntegrityError | None = None
        for _ in range(_RETRY_LIMIT + 1):
            seq = await self._next_seq(session_id)
            event = Event(
                id=uuid.uuid4().hex,
                session_id=session_id,
                seq=seq,
                kind=str(kind),
                author_participant_id=author_participant_id,
                target_participant_id=target_participant_id,
                payload=payload,
            )
            try:
                async with self._session.begin_nested():
                    self._session.add(event)
                    await self._session.flush()
            except IntegrityError as exc:
                last_error = exc
                continue
            return event
        assert last_error is not None
        raise last_error

    async def read(self, session_id: str, *, after_seq: int = 0) -> list[Event]:
        """Transcript window ``seq > after_seq``, ordered."""
        rows = await self._session.execute(
            select(Event)
            .where(Event.session_id == session_id, Event.seq > after_seq)
            .order_by(Event.seq)
        )
        return list(rows.scalars().all())

    async def _next_seq(self, session_id: str) -> int:
        """Next seq for this session (1-based, gap-free). Autoflush makes
        same-session prior appends visible; overridable seam for retry
        testing."""
        current = await self._session.execute(
            select(func.coalesce(func.max(Event.seq), 0)).where(
                Event.session_id == session_id
            )
        )
        return int(current.scalar_one()) + 1
