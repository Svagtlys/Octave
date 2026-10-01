"""Context-plane errors (issue #35).

Independent of ``octave.agent.errors``: the plane ban (design spec
Decision 9) forbids importing the agent plane, so names mirror but do not
share types. The composition root maps whichever it catches from its two
components.
"""

__all__ = [
    "AgentNotFound",
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
