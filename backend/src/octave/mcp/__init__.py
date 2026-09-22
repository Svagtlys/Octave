"""MCP client core — typed façade over the official mcp SDK.

Quarantine rule (see design spec): only ``client.py`` and ``transport.py``
import ``mcp``. Everything re-exported here is Octave-owned; SDK types
never cross this boundary.
"""

from octave.mcp.client import McpClient
from octave.mcp.config import HttpConfig, McpSettings, ServerConfig, StdioConfig
from octave.mcp.deps import get_mcp_manager, get_tool_registry
from octave.mcp.errors import (
    McpConfigError,
    McpConnectionError,
    McpError,
    McpNotConnectedError,
    McpRpcError,
    McpTimeoutError,
)
from octave.mcp.lifespan import mcp_lifespan
from octave.mcp.manager import McpServerManager, ServerState, ServerStatus
from octave.mcp.registry import ServerToolInventory, ToolRegistry
from octave.mcp.types import (
    Notification,
    ServerInfo,
    ToolContent,
    ToolInfo,
    ToolResult,
)

__all__ = [
    "HttpConfig",
    "McpClient",
    "McpConfigError",
    "McpConnectionError",
    "McpError",
    "McpNotConnectedError",
    "McpRpcError",
    "McpServerManager",
    "McpSettings",
    "McpTimeoutError",
    "Notification",
    "ServerConfig",
    "ServerInfo",
    "ServerState",
    "ServerStatus",
    "ServerToolInventory",
    "StdioConfig",
    "ToolContent",
    "ToolInfo",
    "ToolRegistry",
    "ToolResult",
    "get_mcp_manager",
    "get_tool_registry",
    "mcp_lifespan",
]
