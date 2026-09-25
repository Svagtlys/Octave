"""McpToolExecutor: content flattening and McpError -> outcome conversion."""

from typing import Any

import pytest

from octave.agent.mcp_executor import McpToolExecutor
from octave.mcp.errors import (
    McpConfigError,
    McpConnectionError,
    McpNotConnectedError,
    McpRpcError,
    McpTimeoutError,
)
from octave.mcp.types import ToolContent, ToolResult


class StubRegistry:
    """Stands in for ToolRegistry; returns or raises what the test dictates."""

    def __init__(
        self, result: ToolResult | None = None, error: Exception | None = None
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call_tool(
        self, id: str, name: str, arguments: dict[str, Any] | None = None
    ) -> ToolResult:
        self.calls.append((id, name, dict(arguments or {})))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


async def test_joins_text_blocks_and_passes_is_error_through() -> None:
    registry = StubRegistry(
        result=ToolResult(
            content=[ToolContent(kind="text", text="a"), ToolContent(kind="text", text="b")]
        )
    )
    outcome = await McpToolExecutor(registry).call("srv", "read", {"p": 1})  # type: ignore[arg-type]
    assert registry.calls == [("srv", "read", {"p": 1})]
    assert outcome.content == "a\nb"
    assert outcome.is_error is False


async def test_error_result_flags_outcome() -> None:
    registry = StubRegistry(
        result=ToolResult(
            content=[ToolContent(kind="text", text="disk full")], is_error=True
        )
    )
    outcome = await McpToolExecutor(registry).call("srv", "read", {})  # type: ignore[arg-type]
    assert outcome.content == "disk full"
    assert outcome.is_error is True


async def test_empty_content_becomes_sentinel() -> None:
    registry = StubRegistry(result=ToolResult(content=[]))
    outcome = await McpToolExecutor(registry).call("srv", "read", {})  # type: ignore[arg-type]
    assert outcome.content == "(no output)"


@pytest.mark.parametrize(
    "error",
    [
        McpRpcError("bad params", code=-32602),
        McpTimeoutError("timed out after 60s"),
        McpConnectionError("server died"),
        McpConfigError("unknown server id"),
        McpNotConnectedError("not connected"),
    ],
)
async def test_mcp_errors_become_error_outcomes(error: Exception) -> None:
    registry = StubRegistry(error=error)
    outcome = await McpToolExecutor(registry).call("srv", "read", {})  # type: ignore[arg-type]
    assert outcome.is_error is True
    assert str(error) in outcome.content
