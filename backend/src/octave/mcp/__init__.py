"""MCP client core — typed façade over the official mcp SDK.

Quarantine rule (see design spec): only ``client.py`` and ``transport.py``
import ``mcp``. Everything re-exported here is Octave-owned; SDK types
never cross this boundary.
"""

from octave.mcp.client import McpClient
from octave.mcp.config import HttpConfig, McpSettings, ServerConfig, StdioConfig
from octave.mcp.deps import get_mcp_manager
from octave.mcp.errors import (
    McpConfigError,
    McpConnectionError,
    McpError,
    McpNotConnectedError,
    McpRpcError,
    McpTimeoutError,
)
from octave.mcp.manager import McpServerManager, ServerState, ServerStatus
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
    "StdioConfig",
    "ToolContent",
    "ToolInfo",
    "ToolResult",
    "get_mcp_manager",
]
