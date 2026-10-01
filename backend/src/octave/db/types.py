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
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AgentAssignments",
    "AgentStatus",
    "AssistantMessagePayload",
    "EventKind",
    "ExplicitModelBinding",
    "InstanceStatus",
    "ModelBinding",
    "TagModelBinding",
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
    CONTEXT_INJECTION = "context_injection"
    """Harness-authored standing context injected at session start (issue #34).
    Never authored by a participant; targets one agent participant."""


class VaultKind(StrEnum):
    """One vault item's type. Values are stored verbatim in ``vault_items.kind``.

    App-level validation layer (see module docstring): the enum grows freely —
    a new kind costs one member, no migration. Conventions per kind live in
    the context vault data model design spec.
    """

    SKILL = "skill"
    PROMPT = "prompt"
    PREFERENCE = "preference"
    SESSION_SUMMARY = "session_summary"
    TRANSCRIPT_CHUNK = "transcript_chunk"


class AgentStatus(StrEnum):
    """Definition-level gate for spawning and turns. Stored verbatim in
    ``agents.status``. ``paused`` means "do not run this agent anywhere" —
    session-scoped refusal is turn policy, not agent status (design spec)."""

    ACTIVE = "active"
    PAUSED = "paused"


class InstanceStatus(StrEnum):
    """Runtime state of one agent instance. Stored verbatim in
    ``agent_instances.status``. There is no ``failed``: turn failures are
    events in the session, and sessions own ``failed``."""

    IDLE = "idle"
    ACTIVE = "active"


class TagModelBinding(BaseModel):
    """Capability-tag binding, resolved at turn start via model tagging."""

    kind: Literal["tag"]
    tag: str


class ExplicitModelBinding(BaseModel):
    """Direct provider-model pair; adapter existence validated at
    definition-save time (follow-up issue), not at turn start."""

    kind: Literal["explicit"]
    adapter: str
    model: str


ModelBinding = Annotated[
    TagModelBinding | ExplicitModelBinding, Field(discriminator="kind")
]


class AgentAssignments(BaseModel):
    """Named vault-item references assigned to a definition.

    References are app-validated strings; dangling references are tolerated
    at resolution time (skip + warn). Link-table promotion triggers live in
    the design spec. Extra keys allowed, mirroring the ``vault_items.meta``
    convention (ADR 2026-09-20)."""

    model_config = ConfigDict(extra="allow")

    prompt: str | None = None
    skills: list[str] = Field(default_factory=list)
    preference_tags: list[str] = Field(default_factory=list)


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
