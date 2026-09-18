"""Lifecycle manager unit tests — fake clients, no subprocesses, no SDK.

The fake client drives the supervisor deterministically: ``die()``/``hang()``
fire the manager's on_lost hook; ``connect_error``/``ping_error`` script
outcomes. FAST settings keep every sleep in the millisecond range.
"""

from datetime import datetime

import anyio
import pytest

from octave.mcp.client import McpClient
from octave.mcp.config import McpSettings, ServerConfig, StdioConfig
from octave.mcp.errors import (
    McpConfigError,
    McpConnectionError,
    McpError,
    McpNotConnectedError,
    McpTimeoutError,
)
from octave.mcp.manager import McpServerManager, ServerStatus

FAST = McpSettings(
    request_timeout_seconds=0.05,
    restart_base_delay_seconds=0.01,
    restart_max_delay_seconds=0.05,
    restart_max_attempts=3,
    probe_timeout_seconds=0.05,
    stabilization_seconds=0.05,
)

_CONFIG = StdioConfig(command="fake-server")


class FakeClient(McpClient):
    """McpClient stand-in scripting the manager's view of a server."""

    def __init__(self, on_lost: "object | None" = None) -> None:
        super().__init__(settings=FAST, on_lost=on_lost)  # type: ignore[arg-type]
        self.connect_calls = 0
        self.aclose_calls = 0
        self.ping_calls = 0
        self.connect_error: Exception | None = None
        self.ping_error: Exception | None = None

    async def connect(self, config: ServerConfig) -> None:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error

    async def aclose(self) -> None:
        self.aclose_calls += 1

    async def ping(self) -> bool:
        self.ping_calls += 1
        if self.ping_error is not None:
            raise self.ping_error
        return True

    def die(self) -> None:
        """Simulate transport death (what the read-stream monitor does)."""
        assert self._on_lost is not None
        self._on_lost("death")

    def hang(self) -> None:
        """Simulate a request timeout (what _run's timeout branch does)."""
        assert self._on_lost is not None
        self._on_lost("timeout")


class FakeFactory:
    """Builds FakeClients and keeps them for assertions."""

    def __init__(self) -> None:
        self.clients: list[FakeClient] = []

    def __call__(self, on_lost: "object | None" = None) -> FakeClient:
        client = FakeClient(on_lost)
        self.clients.append(client)
        return client


def fail_after_first(factory: FakeFactory):  # noqa: ANN201 - test helper
    """Wrap a factory: each client's initial connect succeeds, later ones fail.

    ``connect`` is wrapped so that after the first successful connect the
    client permanently fails connects — the crash-loop scenario.
    """

    def build(on_lost):  # noqa: ANN001, ANN202 - test helper
        client = factory(on_lost)
        original_connect = client.connect

        async def connect(config: ServerConfig) -> None:
            first = client.connect_calls == 0
            await original_connect(config)
            if first:  # arm permanent failure after the initial connect
                client.connect_error = McpConnectionError("down")

        client.connect = connect  # type: ignore[method-assign]
        return client

    return build


async def wait_for(
    manager: McpServerManager,
    server_id: str,
    predicate,  # noqa: ANN001 - test helper
    timeout: float = 2.0,
) -> ServerStatus:
    """Poll status_of until ``predicate(status)`` holds (supervisor is async).

    Always predicate on observable counters (state + restart_count), never
    bare state — the supervisor can pass through a state the test would
    match before it has acted.
    """
    with anyio.fail_after(timeout):
        while True:
            status = manager.status_of(server_id)
            if predicate(status):
                return status
            await anyio.sleep(0.01)


def make_manager(*ids: str) -> tuple[McpServerManager, FakeFactory]:
    factory = FakeFactory()
    manager = McpServerManager(settings=FAST, client_factory=factory)
    for server_id in ids:
        manager.register(id=server_id, name=f"test-{server_id}", config=_CONFIG)
    return manager, factory


class TestRegistry:
    def test_register_and_status(self) -> None:
        manager, factory = make_manager("a", "b")
        assert [s.id for s in manager.status()] == ["a", "b"]
        status = manager.status_of("a")
        assert status.state == "stopped"
        assert status.name == "test-a"
        assert status.transport == "stdio"
        assert status.restart_count == 0
        assert status.consecutive_failures == 0
        assert status.last_error is None
        assert isinstance(status.last_state_change, datetime)
        assert manager.get_client("a") is factory.clients[0]

    def test_duplicate_id_raises(self) -> None:
        manager, _factory = make_manager("a")
        with pytest.raises(McpConfigError, match="duplicate"):
            manager.register(id="a", name="dupe", config=_CONFIG)

    def test_unknown_id_raises(self) -> None:
        manager, _factory = make_manager("a")
        with pytest.raises(McpConfigError, match="unknown"):
            manager.get_client("nope")
        with pytest.raises(McpConfigError, match="unknown"):
            manager.status_of("nope")

    async def test_register_after_start_all_raises(self) -> None:
        manager, _factory = make_manager("a")
        await manager.start_all()
        try:
            with pytest.raises(McpError):
                manager.register(id="b", name="late", config=_CONFIG)
        finally:
            await manager.stop_all()
