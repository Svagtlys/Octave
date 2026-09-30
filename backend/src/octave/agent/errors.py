"""Orchestration-fatal errors (design spec #79) and lifecycle-gate errors
(design spec 2026-09-27)."""

from octave.inference.types import Message
from octave.tools.errors import ToolError

__all__ = [
    "AgentError",
    "AgentNotFoundError",
    "AgentPausedError",
    "DeciderChoiceError",
    "InstanceExistsError",
    "InstanceNotFoundError",
    "InvalidTransitionError",
    "ModelBindingError",
    "NotAMemberError",
    "RoutingError",
    "SessionNotFoundError",
    "SummaryError",
    "TerminalSessionError",
    "ToolLoopLimitError",
    "TurnInProgressError",
]


class AgentError(Exception):
    """Base for lifecycle-gate failures (design spec 2026-09-27)."""


class AgentNotFoundError(AgentError):
    """No such agent definition."""


class SessionNotFoundError(AgentError):
    """No such session."""


class AgentPausedError(AgentError):
    """Definition-level gate: paused agents do not spawn or take turns."""


class TerminalSessionError(AgentError):
    """Session status is completed/failed/cancelled; no instances spawn."""


class InstanceExistsError(AgentError):
    """One live instance per agent per session (uq constraint)."""


class InstanceNotFoundError(AgentError):
    """No such instance."""


class TurnInProgressError(AgentError):
    """begin_turn lost the idle→active race; the instance is already active."""


class InvalidTransitionError(AgentError):
    """Operation requires a state the instance is not in."""


class ModelBindingError(AgentError):
    """Binding missing, malformed, or (for tag form) unresolvable."""


class RoutingError(AgentError):
    """Base for router failures (design spec 2026-09-28)."""


class NotAMemberError(RoutingError):
    """Author is not a current member of the session (left_at set or
    never invited)."""


class DeciderChoiceError(RoutingError):
    """Decider output malformed or outside the roster. Raised by deciders;
    the driver owns retry + await-user fallback."""


class SummaryError(AgentError):
    """Summary generation produced empty/whitespace content (design spec
    2026-09-29). Never persist empty summaries."""


class ToolLoopLimitError(ToolError):
    """max_tool_rounds exhausted without a final answer.

    ``messages`` is the partial transcript (input + everything appended,
    ending on the unfulfilled assistant tool_calls message) so callers can
    persist partial work or retry.
    """

    def __init__(self, message: str, *, messages: list[Message]) -> None:
        super().__init__(message)
        self.messages = messages
