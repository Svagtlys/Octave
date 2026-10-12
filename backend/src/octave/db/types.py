"""Octave DB domain types.

``events.kind``, ``sessions.status``, and ``vault_items.kind`` are TEXT
columns: the enums here are
the app-level validation layer. A DB ``CHECK`` would force an ``ALTER TABLE``
(a table rebuild on SQLite) for every new kind, so the enum is deliberately
the single source of truth and grows freely.

Only the models with first-class writers ship — the two message payloads and
``context_injection`` (issue #34). ``tool_call`` / ``tool_result`` / ``system``
payloads pass through as validated JSON dicts until their consumers exist.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AgentAssignments",
    "AgentStatus",
    "AssistantMessagePayload",
    "ContextInjectionPayload",
    "EventKind",
    "ExplicitModelBinding",
    "InjectedContextItem",
    "InstanceStatus",
    "ModelBinding",
    "SelectionReason",
    "TagModelBinding",
    "UserMessagePayload",
    "UserRole",
    "UserStatus",
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


class UserRole(StrEnum):
    """Account privilege level. Stored verbatim in ``users.role``. Gates
    account administration only (issue #122) — feature access is uniform for
    all authenticated users."""

    OWNER = "owner"
    MEMBER = "member"


class UserStatus(StrEnum):
    """Account lifecycle. Stored verbatim in ``users.status``. Deactivation
    preserves the user's sessions/vault (reversible); it deletes only the
    user's ``auth_sessions`` rows for instant logout (issue #122)."""

    ACTIVE = "active"
    DEACTIVATED = "deactivated"


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
    convention (ADR 2026-09-20).

    Selection vocabulary (issue #34): ``tags`` is the agent's capability-tag
    set matched against item ``meta.tags``; ``preference_tags`` is a
    deprecated alias folded into :attr:`effective_tags`. ``skills`` and
    ``preference_names`` are explicit by-name selections, additive to the
    tag-matched ones.
    """

    model_config = ConfigDict(extra="allow")

    prompt: str | None = None
    skills: list[str] = Field(default_factory=list)
    preference_tags: list[str] = Field(default_factory=list)
    """DEPRECATED alias of ``tags`` (issue #34). Kept for stored JSON;
    removal is a one-line delete once no writer sets it."""
    tags: list[str] = Field(default_factory=list)
    preference_names: list[str] = Field(default_factory=list)

    @property
    def effective_tags(self) -> list[str]:
        """Case-folded, order-stable dedup union of ``tags`` and the
        deprecated ``preference_tags``. Read-only; excluded from
        serialization (plain property, not a pydantic field)."""
        seen: dict[str, None] = {}
        for tag in [*self.tags, *self.preference_tags]:
            seen.setdefault(tag.lower())
        return list(seen)


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


SelectionReason = Literal["explicit", "global", "agent_tag"]
"""Why an item was selected (issue #34). Extension seam for CM #11:
a future ``retrieved`` member adds found-context provenance."""


class InjectedContextItem(BaseModel):
    """One injected vault item, snapshotted at injection time. ``kind`` is
    the verbatim ``vault_items.kind`` string; ``content`` is the exact prose
    the agent was given (the transcript records what was seen, immune to
    later vault edits)."""

    item_id: str
    kind: str
    name: str
    content: str
    reason: SelectionReason


class ContextInjectionPayload(BaseModel):
    """``context_injection`` event payload: what the harness told one agent
    at session start (issue #34)."""

    agent_id: str
    items: list[InjectedContextItem]
