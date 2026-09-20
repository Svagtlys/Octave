"""Octave DB domain types.

``events.kind``, ``sessions.status``, and ``vault_items.kind`` are TEXT
columns: the enums here are
the app-level validation layer. A DB ``CHECK`` would force an ``ALTER TABLE``
(a table rebuild on SQLite) for every new kind, so the enum is deliberately
the single source of truth and grows freely.

Only the two message payload models ship — the kinds this work item writes.
``tool_call`` / ``tool_result`` / ``system`` payloads pass through as validated
JSON dicts until their consumers exist.
"""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

__all__ = [
    "AssistantMessagePayload",
    "EventKind",
    "UserMessagePayload",
    "VaultKind",
    "VectorHit",
]


class EventKind(StrEnum):
    """One transcript entry's type. Values are stored verbatim in ``events.kind``."""

    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SYSTEM = "system"


class VaultKind(StrEnum):
    """One vault item's type. Values are stored verbatim in ``vault_items.kind``.

    App-level validation layer (see module docstring): the enum grows freely —
    a new kind costs one member, no migration. Conventions per kind live in
    the context vault data model design spec.
    """

    SKILL = "skill"
    PROMPT = "prompt"
    PREFERENCE = "preference"
    RUN_SUMMARY = "run_summary"
    RUN_RECORD = "run_record"


@dataclass(frozen=True)
class VectorHit:
    """One neighbour returned by a similarity search."""

    item_id: str
    distance: float
    """vec0 ``distance`` under the cosine metric: 0 is identical, 2 is opposite."""


class UserMessagePayload(BaseModel):
    """Human input. ``content`` is the text as entered."""

    content: str


class AssistantMessagePayload(BaseModel):
    """LLM output, tagged with the model that produced it when known."""

    content: str
    model_name: str | None = None
