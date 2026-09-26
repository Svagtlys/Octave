"""ToolExecutor over ToolRegistry (issue #79).

The only octave.agent module importing octave.mcp. Converts McpError
subclasses into error ToolOutcomes (Tier-1, model-correctable) so the
loop never aborts on a tool failure.
"""

import logging
from typing import Any

from octave.agent.types import ToolOutcome
from octave.mcp import McpError, ToolRegistry

__all__ = ["McpToolExecutor"]

logger = logging.getLogger(__name__)

_NO_OUTPUT = "(no output)"


class McpToolExecutor:
    """Structural implementation of ``octave.agent.executor.ToolExecutor``."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def call(
        self, server_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> ToolOutcome:
        """Invoke one tool; every failure mode becomes an error outcome."""
        try:
            result = await self._registry.call_tool(server_id, tool_name, arguments)
        except McpError as exc:
            logger.error(
                "MCP tool execution failed | server=%s tool=%s error=%s",
                server_id,
                tool_name,
                exc,
            )
            return ToolOutcome(content=f"Tool execution failed: {exc}", is_error=True)
        content = "\n".join(block.text for block in result.content)
        return ToolOutcome(content=content or _NO_OUTPUT, is_error=result.is_error)
