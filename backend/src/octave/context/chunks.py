"""Verbatim transcript-chunk rendering (issue #35).

Distinct from ``octave.agent.summaries.HeadTailDigest``: that renderer is
summarization budget (truncation, omission markers). This one is the
archival record — full content, no truncation, split only by the
``max_chars`` safety valve (design spec Decisions 4/5).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from octave.context.brackets import TurnBracket
from octave.db.models import Event
from octave.db.types import EventKind

__all__ = ["ChunkPlan", "chunk_item_id", "plan_chunks", "render_bracket"]


@dataclass(frozen=True)
class ChunkPlan:
    """One desired ``transcript_chunk`` item: identity, prose, provenance.
    ``meta`` carries everything except ``model_name`` — the archiver fills
    that from the embed result."""

    item_id: str
    name: str
    content: str
    meta: dict[str, Any]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _event_line(event: Event, label: str) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.kind == EventKind.ASSISTANT_MESSAGE:
        return f"{label}: {payload.get('content', '')}"
    if event.kind == EventKind.TOOL_CALL:
        name = payload.get("tool_name") or payload.get("name") or "tool"
        arguments = payload.get("arguments", "")
        return f"Tool call: {name} {arguments}".rstrip()
    if event.kind == EventKind.TOOL_RESULT:
        content = payload.get("content") or payload.get("result") or _json(
            payload.get("structured", payload)
        )
        return f"Tool result: {content}"
    if event.kind == EventKind.SYSTEM:
        return f"System: {payload.get('content', '')}"
    return _json(payload)


def render_bracket(events: Sequence[Event], label: str) -> str:
    """Full-content labeled lines, seq order, no truncation."""
    ordered = sorted(events, key=lambda e: e.seq)
    return "\n".join(_event_line(e, label) for e in ordered)


def chunk_item_id(
    session_id: str, bracket: TurnBracket, part: int, part_total: int
) -> str:
    """Deterministic id: ``(session_id, seq_range)`` is immutable (events
    never change), so the id doubles as the idempotence key. Split
    brackets carry a ``:part`` suffix."""
    base = f"transcript_chunk:{session_id}:{bracket.seq_start}-{bracket.seq_end}"
    return base if part_total == 1 else f"{base}:{part}"


def _split(text: str, max_chars: int) -> list[str]:
    """Whitespace-boundary split; hard-cut only for a single over-long
    token (guarantees progress: ``cut`` is never 0)."""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        cut = remaining.rfind(" ", 0, max_chars)
        if cut <= 0:
            cut = max_chars
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


def plan_chunks(
    *,
    session_id: str,
    bracket: TurnBracket,
    events: Sequence[Event],
    label: str,
    max_chars: int,
) -> list[ChunkPlan]:
    """One chunk per bracket normally; numbered parts when over max_chars."""
    text = render_bracket(events, label)
    parts = _split(text, max_chars)
    total = len(parts)
    plans: list[ChunkPlan] = []
    for index, part_text in enumerate(parts, start=1):
        name = f"{label} · events {bracket.seq_start}–{bracket.seq_end}"
        if total > 1:
            name += f" (part {index}/{total})"
        plans.append(
            ChunkPlan(
                item_id=chunk_item_id(session_id, bracket, index, total),
                name=name,
                content=part_text,
                meta={
                    "session_id": session_id,
                    "agent_id": bracket.agent_id,
                    "seq_start": bracket.seq_start,
                    "seq_end": bracket.seq_end,
                    "part": index,
                    "part_total": total,
                },
            )
        )
    return plans
