"""MCP tool schemas -> provider-native tool definitions (issue #78).

Pure translation: fleet inventories in, ProviderToolset out. No I/O, no
state, no SDK imports — octave.mcp and octave.inference never import
each other (design spec decision 5).
"""

import copy
import re
from collections.abc import Sequence
from typing import Any

from octave.inference.types import ToolDefinition
from octave.mcp import ServerToolInventory
from octave.tools.errors import ToolNameCollisionError
from octave.tools.types import ProviderToolset, ToolRoute

__all__ = ["translate_tools"]

_NAME_PREFIX = "mcp"
_MAX_NAME_LENGTH = 64
"""OpenAI function-name charset: ^[a-zA-Z0-9_-]{1,64}$."""

_INVALID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def _sanitize(component: str) -> str:
    """Replace characters outside the provider charset with ``_``."""
    return _INVALID_CHARS.sub("_", component)


def _exposed_name(server_name: str, tool_name: str) -> str:
    """``mcp__<server>__<tool>``, sanitized and truncated to 64 chars."""
    name = f"{_NAME_PREFIX}__{_sanitize(server_name)}__{_sanitize(tool_name)}"
    return name[:_MAX_NAME_LENGTH]


def _parameters(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema passthrough minus ``$schema``; deep copy, input untouched.

    Octave never interprets JSON Schema internals (ToolInfo invariant);
    local engines (Ollama/vLLM/llama.cpp) are strict about ``$schema``.
    """
    parameters = copy.deepcopy(schema)
    parameters.pop("$schema", None)
    return parameters


def translate_tools(
    inventories: Sequence[ServerToolInventory],
) -> ProviderToolset:
    """Translate fleet inventories into provider definitions + reverse routes.

    Deterministic: inventories in input order, tools in per-server order.
    Raises ``ToolNameCollisionError`` when two tools expose the same name.
    """
    tools: list[ToolDefinition] = []
    routes: dict[str, ToolRoute] = {}
    for inventory in inventories:
        for tool in inventory.tools:
            name = _exposed_name(inventory.server_name, tool.name)
            if name in routes:
                existing = routes[name]
                raise ToolNameCollisionError(
                    f"Tool name collision on exposed name {name!r}: "
                    f"{existing.server_id}/{existing.tool_name} and "
                    f"{inventory.server_id}/{tool.name}"
                )
            routes[name] = ToolRoute(
                server_id=inventory.server_id, tool_name=tool.name
            )
            tools.append(
                ToolDefinition(
                    name=name,
                    description=tool.description,
                    parameters=_parameters(tool.input_schema),
                )
            )
    return ProviderToolset(tools=tools, routes=routes)
