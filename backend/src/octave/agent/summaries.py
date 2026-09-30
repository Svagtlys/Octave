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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.agent.errors import SessionNotFoundError, SummaryError
from octave.agent.instances import resolve_model
from octave.db.adapter import DbAdapter
from octave.db.event_store import EventStore
from octave.db.models import Event, Participant, Session, VaultItem
from octave.db.types import EventKind, ModelBinding, VaultKind
from octave.db.vault_store import VaultStore
from octave.inference.adapter import InferenceAdapter
from octave.inference.types import CompletionRequest, Message

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

    async def collect(
        self, session_id: str, *, force: bool = False
    ) -> SessionSummary | None:
        """Fresh cached summary, or generate + persist. Empty transcript →
        None (no LLM call, no item written)."""
        session_row = await self._session.get(Session, session_id)
        if session_row is None:
            raise SessionNotFoundError(session_id)
        events = await self._events.read(session_id)
        if not events:
            return None
        seq_max = events[-1].seq
        # Task 5 inserts the cache check here.
        return await self._generate(session_row, events, seq_max)

    async def _generate(
        self, session_row: Session, events: list[Event], seq_max: int
    ) -> SessionSummary:
        labels = await self._labels(events)
        digest_text = await self._digest.digest(
            SummaryContext(session_id=session_row.id, events=events, labels=labels)
        )
        resolved = resolve_model(self._binding, tag_lookup=self._tag_lookup)
        adapter = self._adapter_for(resolved.adapter)
        result = await adapter.complete(
            CompletionRequest(
                model=resolved.model,
                messages=[
                    Message(role="system", content=_SYSTEM_INSTRUCTION),
                    Message(role="user", content=digest_text),
                ],
            )
        )
        text = result.text.strip()
        if not text:
            raise SummaryError(
                f"summary generation for {session_row.id} returned empty content"
            )
        title, body = _parse_summary(text, session_row=session_row, events=events)
        item = await self._vault.upsert(
            item_id=_item_id(session_row.id),
            user_id=session_row.created_by_user_id,
            kind=VaultKind.SESSION_SUMMARY,
            name=title,
            content=body,
            meta={
                "session_id": session_row.id,
                "covered_seq": seq_max,
                "model_name": result.model,
            },
        )
        summary = _to_summary(item, session_row.id)
        assert summary is not None  # written by us; meta is well-formed
        return summary

    async def _labels(self, events: list[Event]) -> dict[str, str]:
        author_ids = {
            event.author_participant_id
            for event in events
            if event.author_participant_id
        }
        if not author_ids:
            return {}
        rows = await self._session.execute(
            select(Participant.id, Participant.label).where(
                Participant.id.in_(author_ids)
            )
        )
        return {participant_id: label for participant_id, label in rows.all()}


_SYSTEM_INSTRUCTION = (
    "You are writing a recap of an agent session for the user who owns it. "
    "Summarize what was done and state the end result. "
    "The first line must be exactly 'Title: <short title>' followed by the "
    "recap body."
)


def _fallback_title(session_row: Session, events: list[Event]) -> str:
    """session.title → first user message (truncated) → Session <id[:8]>."""
    if session_row.title:
        return session_row.title
    for event in events:
        if event.kind == EventKind.USER_MESSAGE:
            content = str(event.payload.get("content", "")).strip()
            if content:
                return content[:60]
    return f"Session {session_row.id[:8]}"


def _parse_summary(
    text: str, *, session_row: Session, events: list[Event]
) -> tuple[str, str]:
    """Split the model's Title line from the body; fall back when the
    contract is ignored. Malformed output never fails a generation."""
    head, sep, rest = text.partition("\n")
    if sep and head.strip().lower().startswith("title:"):
        title = head.strip()[len("Title:"):].strip()
        body = rest.strip()
        if title and body:
            return title, body
    return _fallback_title(session_row, events), text
