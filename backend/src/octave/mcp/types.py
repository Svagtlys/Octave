"""Octave MCP domain types.

These models are the entire vocabulary callers of ``McpClient`` see. SDK
types (``Tool``, ``CallToolResult``, ``Implementation``, …) never appear in
public Octave signatures — translation lives in ``octave.mcp.client``.
"""

from typing import Any, Literal

from pydantic import BaseModel

__all__ = [
    "Notification",
    "ServerInfo",
    "ToolContent",
    "ToolInfo",
    "ToolResult",
]


class ServerInfo(BaseModel):
    """Server identity captured from the initialize handshake."""

    name: str
    version: str
    protocol_version: str


class ToolInfo(BaseModel):
    """One tool as advertised by a server.

    ``input_schema`` stays a raw JSON Schema dict — Octave forwards it to
    inference engines, it never interprets it.
    """

    name: str
    description: str | None = None
    input_schema: dict[str, Any]


class ToolContent(BaseModel):
    """One content block of a tool result. Text only for now; image/audio/
    resource kinds are added when a caller needs them (see spec follow-ups)."""

    kind: Literal["text"]
    text: str


class ToolResult(BaseModel):
    """The result of a tool call. ``is_error`` mirrors MCP-level tool
    failure (distinct from a JSON-RPC error, which raises ``McpRpcError``)."""

    content: list[ToolContent]
    is_error: bool = False


class Notification(BaseModel):
    """An inbound server→client notification, method plus raw params."""

    method: str
    params: dict[str, Any] = {}
