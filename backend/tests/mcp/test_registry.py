"""ToolRegistry unit tests — fake manager + fake clients, no SDK, no subprocess.

Fakes script the registry's view of a server, mirroring the FakeClient
pattern in tests/mcp/test_manager.py: counters + scripted outcomes, no
supervision machinery.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest

from octave.mcp.client import McpClient
from octave.mcp.errors import (
    McpConfigError,
    McpConnectionError,
    McpNotConnectedError,
    McpRpcError,
    McpTimeoutError,
)
from octave.mcp.manager import McpServerManager, ServerState, ServerStatus
from octave.mcp.registry import TOOLS_LIST_CHANGED, ToolRegistry
from octave.mcp.types import Notification, ToolContent, ToolInfo, ToolResult


def _tool(name: str) -> ToolInfo:
    return ToolInfo(name=name, description=name, input_schema={"type": "object"})


async def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll ``predicate`` on a short in-memory sleep until it holds."""
    with anyio.fail_after(timeout):
        while not predicate():
            await anyio.sleep(0.01)


class FakeClient(McpClient):
    """McpClient stand-in scripting tools/list, tools/call, notifications."""

    def __init__(self) -> None:
        super().__init__()
        self.list_tools_calls = 0
        self.tools: list[ToolInfo] = []
        self.list_tools_error: Exception | None = None
        self.list_tools_block: anyio.Event | None = None
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.call_error: Exception | None = None
        self.call_result = ToolResult(
            content=[ToolContent(kind="text", text="ok")]
        )
        self._send, self._recv = anyio.create_memory_object_stream[Notification](
            max_buffer_size=16
        )

    async def list_tools(self) -> list[ToolInfo]:
        self.list_tools_calls += 1
        if self.list_tools_block is not None:
            await self.list_tools_block.wait()
        if self.list_tools_error is not None:
            raise self.list_tools_error
        return list(self.tools)

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> ToolResult:
        self.calls.append((name, arguments))
        if self.call_error is not None:
            raise self.call_error
        return self.call_result

    def notify(self, method: str) -> None:
        """Push an inbound notification (what the listener consumes)."""
        self._send.send_nowait(Notification(method=method))

    def subscribe_notifications(self) -> AsyncIterator[Notification]:
        async def _stream() -> AsyncIterator[Notification]:
            async for notification in self._recv:
                yield notification

        return _stream()


class FakeManager(McpServerManager):
    """Manager stand-in: scripted statuses + fake clients, no supervision."""

    def __init__(self) -> None:
        super().__init__()
        self._fakes: dict[str, tuple[ServerStatus, FakeClient]] = {}

    def add(
        self, id: str, *, name: str, state: ServerState = "connected"
    ) -> FakeClient:
        client = FakeClient()
        status = ServerStatus(
            id=id,
            name=name,
            state=state,
            transport="stdio",
            restart_count=0,
            consecutive_failures=0,
            last_error=None,
            last_state_change=datetime.now(UTC),
        )
        self._fakes[id] = (status, client)
        return client

    def set_status(self, id: str, **changes: Any) -> None:
        """Mutate one server's status (e.g. bump restart_count)."""
        status, client = self._fakes[id]
        self._fakes[id] = (replace(status, **changes), client)

    def status(self) -> list[ServerStatus]:
        return [status for status, _ in self._fakes.values()]

    def status_of(self, id: str) -> ServerStatus:
        if id not in self._fakes:
            raise McpConfigError(f"unknown server id: {id}")
        return self._fakes[id][0]

    def get_client(self, id: str) -> McpClient:
        if id not in self._fakes:
            raise McpConfigError(f"unknown server id: {id}")
        return self._fakes[id][1]


async def test_call_tool_forwards_to_named_server_client() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    registry = ToolRegistry(manager=manager)
    result = await registry.call_tool("s1", "echo", {"text": "hi"})
    assert client.calls == [("echo", {"text": "hi"})]
    assert result.content[0].text == "ok"
    assert result.is_error is False


async def test_call_tool_unknown_server_raises_config_error() -> None:
    registry = ToolRegistry(manager=FakeManager())
    with pytest.raises(McpConfigError):
        await registry.call_tool("nope", "echo")


async def test_call_tool_never_pre_validates_against_cache() -> None:
    """The server is the source of truth — a stale cache must not block calls."""
    manager = FakeManager()
    client = manager.add("s1", name="One")
    registry = ToolRegistry(manager=manager)
    await registry.call_tool("s1", "not_in_inventory")
    assert client.list_tools_calls == 0


@pytest.mark.parametrize(
    "error",
    [
        McpRpcError("bad", code=-32602),
        McpTimeoutError("slow"),
        McpConnectionError("dead"),
        McpNotConnectedError("nope"),
    ],
)
async def test_call_tool_propagates_errors_untranslated(error: Exception) -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.call_error = error
    registry = ToolRegistry(manager=manager)
    with pytest.raises(type(error)):
        await registry.call_tool("s1", "echo")


async def test_call_tool_is_error_result_passes_through() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.call_result = ToolResult(
        content=[ToolContent(kind="text", text="boom")], is_error=True
    )
    registry = ToolRegistry(manager=manager)
    result = await registry.call_tool("s1", "fail")
    assert result.is_error is True
