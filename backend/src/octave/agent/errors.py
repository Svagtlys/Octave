"""Orchestration-fatal errors (design spec #79)."""

from octave.inference.types import Message
from octave.tools.errors import ToolError

__all__ = ["ToolLoopLimitError"]


class ToolLoopLimitError(ToolError):
    """max_tool_rounds exhausted without a final answer.

    ``messages`` is the partial transcript (input + everything appended,
    ending on the unfulfilled assistant tool_calls message) so callers can
    persist partial work or retry.
    """

    def __init__(self, message: str, *, messages: list[Message]) -> None:
        super().__init__(message)
        self.messages = messages
