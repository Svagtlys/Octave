"""Agent orchestration vocabulary (design spec #79)."""

from pydantic import BaseModel

from octave.inference.types import CompletionResult, Message

__all__ = ["ToolOutcome", "ToolTurn"]


class ToolOutcome(BaseModel):
    """Flattened result of one tool execution."""

    content: str
    is_error: bool = False


class ToolTurn(BaseModel):
    """Result of one orchestrated turn."""

    messages: list[Message]
    """Input + appended assistant/tool messages + final assistant message."""

    result: CompletionResult
    """The final (no tool_calls) completion."""

    tool_rounds: int
    """Tool-execution rounds performed."""
