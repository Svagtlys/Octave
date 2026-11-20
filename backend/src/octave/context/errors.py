"""Context-plane errors (issue #35).

Independent of ``octave.agent.errors``: the plane ban (design spec
Decision 9) forbids importing the agent plane, so names mirror but do not
share types. The composition root maps whichever it catches from its two
components.
"""

from octave.db.types import SelectionReason

__all__ = [
    "AgentNotFound",
    "ContextBudgetExceededError",
    "ContextError",
    "ModelBindingNotResolved",
    "ParticipantNotFound",
    "SessionNotFound",
]


class ContextError(Exception):
    """Base for octave.context errors."""


class SessionNotFound(ContextError):
    """No sessions row for the requested id."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"session {session_id!r} not found")
        self.session_id = session_id


class ModelBindingNotResolved(ContextError):
    """Embed binding unresolvable (tag form without a lookup, or tag miss).
    Mirrors the shipped ``ModelBindingError`` fail-loud policy."""


class AgentNotFound(ContextError):
    """No agents row for the requested id (issue #34). Mirrors the agent
    plane's error name with an independent type (plane ban)."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"agent {agent_id!r} not found")
        self.agent_id = agent_id


class ParticipantNotFound(ContextError):
    """The agent has no participant row: not spawned into any session.
    Fail loud rather than write an untargeted injection (issue #34)."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"agent {agent_id!r} has no participant row")
        self.agent_id = agent_id


class ContextBudgetExceededError(ContextError):
    """Selected standing context exceeds the token budget (issue #36).
    Nothing was dropped and nothing was written — trim the agent's
    assignments/tags and re-run session start. ``drop_hint`` lists every
    selected item as ``(name, reason)``; the message groups them
    lowest-precedence first (agent_tag → global → explicit) as the trim
    order (Decision 5)."""

    def __init__(
        self,
        *,
        session_id: str,
        agent_id: str,
        budget_tokens: int,
        estimated_tokens: int,
        drop_hint: list[tuple[str, SelectionReason]],
    ) -> None:
        self.session_id = session_id
        self.agent_id = agent_id
        self.budget_tokens = budget_tokens
        self.estimated_tokens = estimated_tokens
        self.drop_hint = drop_hint
        parts: list[str] = []
        for reason in ("agent_tag", "global", "explicit"):
            names = [name for name, r in drop_hint if r == reason]
            if names:
                parts.append(f"{reason}: {', '.join(names)}")
        hint = "; trim " + "; then ".join(parts) if parts else ""
        super().__init__(
            f"agent {agent_id!r} in session {session_id!r}: standing context "
            f"~{estimated_tokens} tokens exceeds budget {budget_tokens}{hint}"
        )
