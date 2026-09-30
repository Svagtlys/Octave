"""Context-plane errors (issue #35).

Independent of ``octave.agent.errors``: the plane ban (design spec
Decision 9) forbids importing the agent plane, so names mirror but do not
share types. The composition root maps whichever it catches from its two
components.
"""

__all__ = ["ContextError", "ModelBindingNotResolved", "SessionNotFound"]


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
