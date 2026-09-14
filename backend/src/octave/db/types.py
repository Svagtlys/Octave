"""Octave DB domain types.

``events.kind`` and ``sessions.status`` are TEXT columns: the enums here are
the app-level validation layer. A DB ``CHECK`` would force an ``ALTER TABLE``
(a table rebuild on SQLite) for every new kind, so the enum is deliberately
the single source of truth and grows freely.

Only the two message payload models ship — the kinds this work item writes.
``tool_call`` / ``tool_result`` / ``system`` payloads pass through as validated
JSON dicts until their consumers exist.
"""

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel

__all__ = [
    "AssistantMessagePayload",
    "EventKind",
    "UserMessagePayload",
    "VectorHit",
]


class EventKind(str, Enum):
    """One transcript entry's type. Values are stored verbatim in ``events.kind``."""

    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SYSTEM = "system"


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
