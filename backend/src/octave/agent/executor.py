"""Tool-execution seam (design spec #79 decision 7)."""

from typing import Any, Protocol

from octave.agent.types import ToolOutcome

__all__ = ["ToolExecutor"]


class ToolExecutor(Protocol):
    """Executes one resolved tool call.

    Implementations must not raise for tool-level failures — return
    ``ToolOutcome(is_error=True)`` so the model can self-correct.
    """

    async def call(
        self, server_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> ToolOutcome: ...
