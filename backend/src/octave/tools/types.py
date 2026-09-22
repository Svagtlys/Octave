"""Translator output vocabulary (design spec #78)."""

from pydantic import BaseModel

from octave.inference.types import ToolDefinition

__all__ = ["ProviderToolset", "ToolRoute"]


class ToolRoute(BaseModel):
    """Reverse-resolution target for one exposed tool name."""

    server_id: str
    """Registry addressing key: ``ToolRegistry.call_tool(server_id, ...)``."""

    tool_name: str
    """Original (unprefixed, unsanitized) MCP tool name."""


class ProviderToolset(BaseModel):
    """Translator output: what the engine sees + how to route calls back."""

    tools: list[ToolDefinition]
    routes: dict[str, ToolRoute]
    """Exposed name -> route. Keys match ``tools[*].name`` exactly."""
