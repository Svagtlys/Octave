"""ToolRegistry unit tests — fake manager + fake clients, no SDK, no subprocess.

Fakes script the registry's view of a server, mirroring the FakeClient
pattern in tests/mcp/test_manager.py: counters + scripted outcomes, no
supervision machinery.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import replace
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


async def test_tools_for_unpopulated_fetches_synchronously() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    tools = await registry.tools_for("s1")
    assert [t.name for t in tools] == ["echo"]
    assert client.list_tools_calls == 1


async def test_inventory_reports_freshness_metadata() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    [inv] = await registry.inventory()
    assert inv.server_id == "s1"
    assert inv.server_name == "One"
    assert inv.state == "connected"
    assert inv.fetched_at is not None
    assert inv.last_error is None


async def test_discovery_failure_records_error_and_never_raises() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.list_tools_error = McpTimeoutError("boom")
    registry = ToolRegistry(manager=manager)
    tools = await registry.tools_for("s1")
    assert tools == []
    [inv] = await registry.inventory()
    assert inv.fetched_at is None  # failure does not mark the entry fresh
    assert inv.last_error == "boom"


async def test_last_known_good_survives_later_failure() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    await registry.tools_for("s1")
    first_fetched_at = registry_status_fetched_at(registry)
    client.tools = []
    client.list_tools_error = McpConnectionError("died")
    manager.set_status("s1", restart_count=1)  # force staleness
    [inv] = await registry.inventory()
    assert [t.name for t in inv.tools] == ["echo"]  # kept, not wiped
    assert inv.last_error == "died"
    assert inv.fetched_at == first_fetched_at  # timestamp of last SUCCESS


def registry_status_fetched_at(registry: ToolRegistry):  # noqa: ANN201 - test helper
    """Grab the entry's fetched_at via the (sync) internal cache."""
    return next(iter(registry._entries.values())).fetched_at  # noqa: SLF001


async def test_recovered_server_clears_error_on_next_read() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.list_tools_error = McpTimeoutError("boom")
    registry = ToolRegistry(manager=manager)
    await registry.tools_for("s1")
    client.list_tools_error = None
    client.tools = [_tool("echo")]
    [inv] = await registry.inventory()  # still stale (never succeeded) -> retry
    assert inv.last_error is None
    assert [t.name for t in inv.tools] == ["echo"]


async def test_refresh_unknown_server_raises_config_error() -> None:
    registry = ToolRegistry(manager=FakeManager())
    with pytest.raises(McpConfigError):
        await registry.refresh("nope")


async def test_fresh_entry_is_served_from_cache_without_wire_traffic() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    await registry.tools_for("s1")
    await registry.tools_for("s1")
    await registry.inventory()
    assert client.list_tools_calls == 1


async def test_restart_invalidates_entry() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    await registry.tools_for("s1")
    manager.set_status("s1", restart_count=1)
    await registry.tools_for("s1")
    assert client.list_tools_calls == 2


async def test_concurrent_stale_reads_coalesce_into_one_fetch() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    client.list_tools_block = anyio.Event()
    registry = ToolRegistry(manager=manager)

    results: list[int] = []

    async def reader() -> None:
        tools = await registry.tools_for("s1")
        results.append(len(tools))

    async with anyio.create_task_group() as tg:
        for _ in range(5):
            tg.start_soon(reader)
        await anyio.sleep(0.05)  # one reader holds the lock inside list_tools
        client.list_tools_block.set()

    assert client.list_tools_calls == 1
    assert results == [1] * 5


async def test_warm_up_waits_for_connection_then_populates() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One", state="starting")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    await registry.start()
    try:
        await anyio.sleep(0.05)
        assert client.list_tools_calls == 0  # still starting: zero traffic
        manager.set_status("s1", state="connected")
        await wait_for(lambda: client.list_tools_calls == 1)
    finally:
        await registry.stop()


async def test_list_changed_notification_triggers_refresh() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    await registry.start()
    try:
        await wait_for(lambda: client.list_tools_calls == 1)  # warm-up fetch
        client.notify(TOOLS_LIST_CHANGED)
        await wait_for(lambda: client.list_tools_calls == 2)
    finally:
        await registry.stop()


async def test_unrelated_notifications_do_not_refresh() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    await registry.start()
    try:
        await wait_for(lambda: client.list_tools_calls == 1)
        client.notify("notifications/message")
        await anyio.sleep(0.05)
        assert client.list_tools_calls == 1
    finally:
        await registry.stop()


async def test_stop_is_idempotent() -> None:
    manager = FakeManager()
    manager.add("s1", name="One")
    registry = ToolRegistry(manager=manager)
    await registry.start()
    await registry.stop()
    await registry.stop()  # must not raise or hang


async def test_refresh_all_covers_every_server_and_isolates_failures() -> None:
    manager = FakeManager()
    good = manager.add("s1", name="One")
    good.tools = [_tool("echo")]
    bad = manager.add("s2", name="Two")
    bad.list_tools_error = McpTimeoutError("boom")
    registry = ToolRegistry(manager=manager)
    await registry.refresh_all()
    inventories = {inv.server_id: inv for inv in await registry.inventory()}
    assert [t.name for t in inventories["s1"].tools] == ["echo"]
    assert inventories["s1"].last_error is None
    assert inventories["s2"].last_error == "boom"  # failure isolated, s1 intact
    assert good.list_tools_calls >= 1
    assert bad.list_tools_calls >= 1
