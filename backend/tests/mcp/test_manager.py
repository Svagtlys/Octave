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


class TestSupervision:
    async def test_start_all_connects_and_stop_all_closes(self) -> None:
        manager, factory = make_manager("a", "b")
        await manager.start_all()
        try:
            await wait_for(manager, "a", lambda s: s.state == "connected")
            await wait_for(manager, "b", lambda s: s.state == "connected")
        finally:
            await manager.stop_all()
        assert manager.status_of("a").state == "stopped"
        assert manager.status_of("b").state == "stopped"
        assert all(c.aclose_calls >= 1 for c in factory.clients)

    async def test_death_triggers_auto_restart(self) -> None:
        manager, factory = make_manager("a")
        await manager.start_all()
        try:
            await wait_for(manager, "a", lambda s: s.state == "connected")
            factory.clients[0].die()
            # Predicate on restart_count, not bare state: the supervisor
            # passes through connected→restarting→connected and a bare
            # "connected" wait could return before it even wakes.
            status = await wait_for(
                manager, "a", lambda s: s.state == "connected" and s.restart_count == 1
            )
            assert status.consecutive_failures == 1  # not reset yet
        finally:
            await manager.stop_all()

    async def test_connect_failure_backs_off_then_crashes(self) -> None:
        manager, factory = make_manager("a")
        factory.clients[0].connect_error = McpConnectionError("boom")
        await manager.start_all()
        try:
            status = await wait_for(manager, "a", lambda s: s.state == "crashed")
            assert status.consecutive_failures == FAST.restart_max_attempts
            assert "boom" in (status.last_error or "")
            # initial attempt + restart_max_attempts retries, no more:
            assert factory.clients[0].connect_calls == 1 + FAST.restart_max_attempts
        finally:
            await manager.stop_all()

    async def test_stop_cancels_supervisor_mid_backoff(self) -> None:
        factory = FakeFactory()
        manager = McpServerManager(
            settings=FAST, client_factory=fail_after_first(factory)
        )
        manager.register(id="a", name="a", config=_CONFIG)
        await manager.start_all()
        await wait_for(manager, "a", lambda s: s.state == "connected")
        factory.clients[0].die()  # supervisor enters backoff; retries all fail
        # FAST backoff totals ~70ms before crash — accept either mid-cycle
        # state; the point is stop() works while the supervisor is busy.
        await wait_for(manager, "a", lambda s: s.state in ("restarting", "crashed"))
        await manager.stop("a")
        status = await wait_for(manager, "a", lambda s: s.state == "stopped")
        assert status.state == "stopped"
        assert factory.clients[0].aclose_calls >= 1  # shielded finally ran

    async def test_manual_restart_on_stopped_raises(self) -> None:
        manager, _factory = make_manager("a")
        with pytest.raises(McpNotConnectedError):
            await manager.restart("a")

    async def test_manual_restart_exits_crashed(self) -> None:
        factory = FakeFactory()
        manager = McpServerManager(
            settings=FAST, client_factory=fail_after_first(factory)
        )
        manager.register(id="a", name="a", config=_CONFIG)
        await manager.start_all()
        factory.clients[0].die()
        status = await wait_for(manager, "a", lambda s: s.state == "crashed")
        assert status.consecutive_failures == FAST.restart_max_attempts
        # Recovery: clear the scripted failure, manual restart cycles.
        factory.clients[0].connect_error = None
        await manager.restart("a")
        status = await wait_for(
            manager,
            "a",
            lambda s: s.state == "connected" and s.consecutive_failures == 0,
        )
        assert status.restart_count >= 1
        await manager.stop_all()

    async def test_supervisor_exception_contains_to_crashed(self) -> None:
        factory = FakeFactory()
        manager = McpServerManager(settings=FAST, client_factory=factory)
        manager.register(id="a", name="a", config=_CONFIG)
        factory.clients[0].connect_error = RuntimeError("manager bug")
        await manager.start_all()
        try:
            status = await wait_for(manager, "a", lambda s: s.state == "crashed")
            assert status.state == "crashed"
        finally:
            await manager.stop_all()


class TestProbeAndStabilization:
    async def test_timeout_probe_ok_keeps_server_connected(self) -> None:
        manager, factory = make_manager("a")
        await manager.start_all()
        try:
            await wait_for(manager, "a", lambda s: s.state == "connected")
            factory.clients[0].hang()  # request timed out; server answers ping
            await anyio.sleep(0.1)  # give the supervisor time to act
            status = manager.status_of("a")
            assert status.state == "connected"
            assert status.restart_count == 0
            assert factory.clients[0].ping_calls == 1
            assert factory.clients[0].aclose_calls == 0
        finally:
            await manager.stop_all()

    async def test_timeout_probe_failure_restarts(self) -> None:
        manager, factory = make_manager("a")
        await manager.start_all()
        try:
            await wait_for(manager, "a", lambda s: s.state == "connected")
            factory.clients[0].ping_error = McpTimeoutError("ping wedged")
            factory.clients[0].hang()
            status = await wait_for(
                manager, "a", lambda s: s.state == "connected" and s.restart_count == 1
            )
            assert status.restart_count == 1
        finally:
            await manager.stop_all()

    async def test_stabilization_resets_failure_counter(self) -> None:
        manager, factory = make_manager("a")
        await manager.start_all()
        try:
            factory.clients[0].die()  # first crash → restart #1, streak starts
            await wait_for(
                manager, "a", lambda s: s.state == "connected" and s.restart_count == 1
            )
            assert manager.status_of("a").consecutive_failures == 1
            factory.clients[0].die()  # re-die inside the stabilization window
            await wait_for(
                manager, "a", lambda s: s.state == "connected" and s.restart_count == 2
            )
            assert manager.status_of("a").consecutive_failures == 2  # escalated
            await anyio.sleep(FAST.stabilization_seconds + 0.05)
            factory.clients[0].die()  # died after holding past the window
            status = await wait_for(
                manager, "a", lambda s: s.state == "connected" and s.restart_count == 3
            )
            assert status.consecutive_failures == 1  # counter reset
        finally:
            await manager.stop_all()

    async def test_multi_server_independence(self) -> None:
        manager, factory = make_manager("bad", "good")
        factory.clients[0].connect_error = McpConnectionError("bad binary")
        await manager.start_all()
        try:
            await wait_for(manager, "bad", lambda s: s.state == "crashed")
            await wait_for(manager, "good", lambda s: s.state == "connected")
            factory.clients[1].die()  # 'good' recovers despite 'bad' crashed
            await wait_for(
                manager,
                "good",
                lambda s: s.state == "connected" and s.restart_count == 1,
            )
        finally:
            await manager.stop_all()
