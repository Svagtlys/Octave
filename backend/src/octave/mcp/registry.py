"""MCP tool registry — fleet-wide tool inventory + execution surface.

Role (design spec #77): the tool-plane counterpart to the manager's
lifecycle-plane. Owns the per-server inventory cache, event-driven
invalidation (restart_count drift, notifications/tools/list_changed,
explicit refresh), and the (server_id, tool_name) call surface. Composes
on ``McpServerManager``'s public API only; never imports the ``mcp`` SDK.
"""

import logging
from typing import Any

from octave.mcp.manager import McpServerManager
from octave.mcp.types import ToolResult

__all__ = ["ServerToolInventory", "ToolRegistry"]

logger = logging.getLogger(__name__)

TOOLS_LIST_CHANGED = "notifications/tools/list_changed"
"""MCP notification method signalling a server's tool-set changed."""


class ToolRegistry:
    """Fleet-wide tool inventory + execution surface over a manager."""

    def __init__(self, *, manager: McpServerManager) -> None:
        self._manager = manager

    async def call_tool(
        self, id: str, name: str, arguments: dict[str, Any] | None = None
    ) -> ToolResult:
        """Invoke a tool on one server; errors propagate untranslated.

        No cache pre-validation: the server is the source of truth, and a
        stale inventory must never block a valid call. Timeout, JSON-RPC,
        and connection semantics are the client's (``McpClient.call_tool``).
        """
        client = self._manager.get_client(id)  # unknown id -> McpConfigError
        return await client.call_tool(name, arguments=arguments)
