"""Octave MCP exception hierarchy.

Wire-layer exceptions (the ``mcp`` SDK, ``httpx``, OS spawn failures) must
never escape ``octave.mcp.client``; they are translated at that boundary.
Same fail-loud philosophy as ``octave.inference.errors``.
"""

from typing import Any

__all__ = [
    "McpConfigError",
    "McpConnectionError",
    "McpError",
    "McpNotConnectedError",
    "McpRpcError",
    "McpTimeoutError",
]


class McpError(Exception):
    """Base class for every MCP client failure."""


class McpConnectionError(McpError):
    """The server could not be reached or spawned."""


class McpNotConnectedError(McpError):
    """A method was used before connect() or after aclose()."""


class McpTimeoutError(McpError):
    """A request exceeded the configured timeout; the request is abandoned."""


class McpConfigError(McpError):
    """Server configuration is malformed (raised before any I/O)."""


class McpRpcError(McpError):
    """A JSON-RPC 2.0 error response from the server.

    ``code`` is the JSON-RPC code verbatim so callers can branch on the
    standard set: -32700 parse error, -32600 invalid request, -32601 method
    not found, -32602 invalid params, -32603 internal error, and the
    -32000..-32099 server-error range MCP uses.
    """

    def __init__(self, message: str, *, code: int, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data
