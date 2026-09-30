"""Session summaries: derived, cached, LLM-written session overviews (issue #28).

The transcript (``events``) is the canonical record; this module produces the
Tier-1 headline — one summary per session covering what was done and the end
result — cached as a ``VaultKind.SESSION_SUMMARY`` vault item under the
deterministic id ``session_summary:<session_id>``. Follows the VaultStore
convention: constructed with the caller's AsyncSession, never commits. The
LLM call happens before any DB write; generation is out-of-band, never
inside ``MessageRouter.deliver()``.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from octave.db.adapter import DbAdapter
from octave.db.event_store import EventStore
from octave.db.models import Event, VaultItem
from octave.db.types import EventKind, ModelBinding
from octave.db.vault_store import VaultStore
from octave.inference.adapter import InferenceAdapter

__all__ = [
    "HeadTailDigest",
    "SessionSummarizer",
    "SessionSummary",
    "SummaryContext",
    "TranscriptDigest",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SummaryContext:
    """Material handed to a digest strategy. ``events`` is the full
    transcript, already read; strategies may consult further sources through
    their own seams — ctx is the baseline, not a leash."""

    session_id: str
    events: list[Event]
    labels: dict[str, str]  # participant_id -> display label


class TranscriptDigest(Protocol):
    """Produces the model-facing material for one summary."""

    async def digest(self, ctx: SummaryContext) -> str: ...


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _render_line(event: Event, labels: dict[str, str], tool_line_chars: int) -> str:
    """One transcript entry -> one digest line."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    kind = event.kind
    if kind == EventKind.USER_MESSAGE:
        return f"User: {payload.get('content', '')}"
    if kind == EventKind.ASSISTANT_MESSAGE:
        label = labels.get(event.author_participant_id or "", "Assistant")
        return f"{label}: {payload.get('content', '')}"
    if kind == EventKind.SYSTEM:
        return f"System: {payload.get('content', '')}"
    if kind == EventKind.TOOL_CALL:
        name = payload.get("tool_name") or payload.get("name") or "tool"
        arguments = payload.get("arguments", "")
        return _truncate(f"Tool call: {name} {arguments}", tool_line_chars)
    # TOOL_RESULT and any future kind: content-ish, compact.
    content = payload.get("content") or payload.get("result") or ""
    return _truncate(f"Tool result: {content}", tool_line_chars)


class HeadTailDigest:
    """Default strategy. Whole transcript when
    ``len(events) <= head_events + tail_events``; else first ``head_events``
    + last ``tail_events`` with a ``… N earlier events omitted …`` marker
    between them (N = number omitted)."""

    def __init__(
        self,
        *,
        head_events: int = 10,
        tail_events: int = 40,
        tool_line_chars: int = 200,
    ) -> None:
        self._head_events = head_events
        self._tail_events = tail_events
        self._tool_line_chars = tool_line_chars

    async def digest(self, ctx: SummaryContext) -> str:
        events = ctx.events
        if len(events) <= self._head_events + self._tail_events:
            lines = [
                _render_line(e, ctx.labels, self._tool_line_chars) for e in events
            ]
        else:
            head = events[: self._head_events]
            tail = events[-self._tail_events :]
            omitted = len(events) - len(head) - len(tail)
            lines = [
                _render_line(e, ctx.labels, self._tool_line_chars) for e in head
            ]
            lines.append(f"… {omitted} earlier events omitted …")
            lines.extend(
                _render_line(e, ctx.labels, self._tool_line_chars) for e in tail
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class SessionSummary:
    """One session's derived headline. ``covered_seq`` is the transcript
    seq at generation time — freshness is computed, never stored."""

    session_id: str
    owner_user_id: str
    title: str
    content: str
    covered_seq: int
    model_name: str | None
    generated_at: datetime


def _item_id(session_id: str) -> str:
    """Deterministic vault id: regeneration is an idempotent replacement."""
    return f"session_summary:{session_id}"


def _to_summary(item: VaultItem, session_id: str) -> SessionSummary | None:
    """Lenient by design (reporting surface): malformed cached meta is
    treated as missing, mirroring the registry's _parse_assignments rule."""
    meta = item.meta if isinstance(item.meta, dict) else {}
    covered = meta.get("covered_seq")
    if not isinstance(covered, int):
        return None
    model_name = meta.get("model_name")
    return SessionSummary(
        session_id=session_id,
        owner_user_id=item.user_id,
        title=item.name,
        content=item.content,
        covered_seq=covered,
        model_name=model_name if isinstance(model_name, str) else None,
        generated_at=item.updated_at,
    )


class SessionSummarizer:
    """Generate + cache one LLM summary per session. Never commits; callers
    own transaction boundaries (``octave.db.deps``)."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        db_adapter: DbAdapter,
        binding: ModelBinding,
        adapter_for: Callable[[str | None], InferenceAdapter],
        tag_lookup: Callable[[str], str | None] | None = None,
        digest: TranscriptDigest | None = None,
    ) -> None:
        self._session = session
        self._binding = binding
        self._adapter_for = adapter_for
        self._tag_lookup = tag_lookup
        self._digest = digest if digest is not None else HeadTailDigest()
        self._events = EventStore(session)
        self._vault = VaultStore(db_adapter, session)

    async def peek(self, session_id: str) -> SessionSummary | None:
        """Cached summary only; never calls the adapter. The cheap path for
        list views."""
        item = await self._vault.get(_item_id(session_id))
        if item is None:
            return None
        return _to_summary(item, session_id)
