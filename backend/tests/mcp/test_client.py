"""McpClient lifecycle: handshake, server_info, ping, connect/close guards."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import pytest
from anyio.streams.memory import (
    MemoryObjectReceiveStream,
    MemoryObjectSendStream,
)
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage
from mcp.types import (
    LATEST_PROTOCOL_VERSION,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCRequest,
    JSONRPCResponse,
)

from octave.mcp.client import McpClient
from octave.mcp.config import McpSettings, ServerConfig, StdioConfig
from octave.mcp.errors import (
    McpConnectionError,
    McpError,
    McpNotConnectedError,
    McpRpcError,
    McpTimeoutError,
)
from octave.mcp.transport import TransportStreams
from octave.mcp.types import Notification, ToolContent, ToolResult

from .conftest import build_server

_INIT_RESULT = {
    "protocolVersion": LATEST_PROTOCOL_VERSION,
    "capabilities": {},
    "serverInfo": {"name": "stub", "version": "0.0"},
}


async def test_connect_runs_initialize_and_captures_server_info(harness: Any) -> None:
    async with harness() as (client, _peer):
        assert client.server_info.name == "harness"
        assert client.server_info.version
        assert client.server_info.protocol_version


async def test_ping_round_trips(harness: Any) -> None:
    async with harness() as (client, _peer):
        assert await client.ping() is True


async def test_ping_before_connect_raises_not_connected() -> None:
    client = McpClient()
    with pytest.raises(McpNotConnectedError):
        await client.ping()


def test_server_info_before_connect_raises_not_connected() -> None:
    with pytest.raises(McpNotConnectedError):
        _ = McpClient().server_info


async def test_methods_after_aclose_raise_not_connected(harness: Any) -> None:
    async with harness() as (client, _peer):
        pass  # harness ran aclose() for us
    with pytest.raises(McpNotConnectedError):
        await client.ping()


async def test_double_connect_raises(harness: Any) -> None:
    async with harness() as (client, _peer):
        with pytest.raises(McpError):
            await client.connect(StdioConfig(command="again"))


async def test_aclose_is_idempotent(harness: Any) -> None:
    async with harness() as (client, _peer):
        await client.aclose()
        await client.aclose()


async def test_list_tools_maps_to_octave_types(harness: Any) -> None:
    async with harness() as (client, _peer):
        tools = await client.list_tools()
    assert [tool.name for tool in tools] == ["echo", "slow", "tool_error"]
    assert tools[0].description == "echo"
    assert tools[0].input_schema["type"] == "object"


async def test_call_tool_maps_text_content(harness: Any) -> None:
    async with harness() as (client, _peer):
        result = await client.call_tool("echo", {"text": "hi"})
    assert result == ToolResult(content=[ToolContent(kind="text", text="hi")])


async def test_call_tool_surfaces_mcp_level_error_flag(harness: Any) -> None:
    async with harness() as (client, _peer):
        result = await client.call_tool("tool_error")
    assert result.is_error is True
    assert result.content[0].text == "tool failed"


async def test_unknown_tool_raises_rpc_error_with_wire_code(harness: Any) -> None:
    async with harness() as (client, _peer):
        with pytest.raises(McpRpcError) as excinfo:
            await client.call_tool("nope")
    assert excinfo.value.code == -32601


async def test_connect_spawn_failure_raises_connection_error() -> None:
    # Real transport (no harness): a non-existent binary must fail at spawn.
    client = McpClient()
    with pytest.raises(McpConnectionError):
        await client.connect(StdioConfig(command="/nonexistent/octave-mcp-binary"))


async def test_send_request_hits_raw_jsonrpc(harness: Any) -> None:
    async with harness() as (client, _peer):
        assert await client.send_request("ping") == {}


async def test_send_request_unknown_method_raises_rpc_error(harness: Any) -> None:
    async with harness() as (client, _peer):
        with pytest.raises(McpRpcError) as excinfo:
            await client.send_request("octave/definitely-not-a-method")
    # SDK 1.30 validates inbound requests against the typed ClientRequest
    # union before dispatch: an unrecognized custom method fails that
    # validation and comes back as -32602, never reaching the -32601
    # no-handler path. Exact verbatim code propagation is pinned by the
    # -32601 call_tool test.
    assert excinfo.value.code == -32602


async def test_send_notification_is_fire_and_forget(harness: Any) -> None:
    async with harness() as (client, _peer):
        # A method the SDK server understands and silently handles.
        await client.send_notification(
            "notifications/cancelled", {"requestId": "x", "reason": "smoke"}
        )


async def test_slow_tool_times_out_and_session_survives(harness: Any) -> None:
    async with harness(request_timeout=0.5) as (client, _peer):
        with pytest.raises(McpTimeoutError):
            await client.call_tool("slow")
        # Abandoning the request must not poison the session (design spec).
        assert await client.ping() is True


async def test_server_notification_reaches_subscriber(harness: Any) -> None:
    async with harness() as (client, peer):
        subscriber = client.subscribe_notifications()
        # SDK 1.30 validates inbound notifications against the typed
        # ServerNotification union and drops unrecognized custom methods
        # before message_handler is reached, so fan-out carries standard
        # MCP notifications; a custom octave/* method never arrives.
        await peer.send_notification(
            "notifications/message", {"level": "info", "data": {"hello": "world"}}
        )
        notification = await subscriber.__anext__()
        assert notification == Notification(
            method="notifications/message",
            params={"level": "info", "data": {"hello": "world"}},
        )
        await subscriber.aclose()


async def test_concurrent_calls_correlate_responses(harness: Any) -> None:
    async with harness() as (client, _peer):
        results: list[str] = [""] * 10

        async def _call(index: int) -> None:
            result = await client.call_tool("echo", {"text": f"ping-{index}"})
            results[index] = result.content[0].text

        async with anyio.create_task_group() as tg:
            for i in range(10):
                tg.start_soon(_call, i)

    assert results == [f"ping-{i}" for i in range(10)]


async def test_inbound_request_rejected_with_method_not_found() -> None:
    """Server→client requests get JSON-RPC -32601 (SDK default; decision 4).

    Standalone stub peer — no harness server — so the client's error response
    is read directly instead of racing the server's receive loop.
    """
    async with create_client_server_memory_streams() as (
        (cread, cwrite),
        (sread, swrite),
    ):

        @asynccontextmanager
        async def _factory(_config: ServerConfig) -> AsyncIterator[TransportStreams]:
            yield TransportStreams(read=cread, write=cwrite)

        client = McpClient(
            transport_factory=_factory,
            settings=McpSettings(request_timeout_seconds=5.0),
        )
        captured: list[JSONRPCMessage] = []
        got_response = anyio.Event()

        async def _stub_peer() -> None:
            async for message in sread:
                root = message.message.root
                if isinstance(root, JSONRPCRequest) and root.method == "initialize":
                    await swrite.send(
                        SessionMessage(
                            JSONRPCMessage(
                                root=JSONRPCResponse(
                                    jsonrpc="2.0", id=root.id, result=_INIT_RESULT
                                )
                            )
                        )
                    )
                elif isinstance(root, JSONRPCResponse | JSONRPCError):
                    # SDK 1.30 answers failures with a JSONRPCError envelope.
                    captured.append(message.message)
                    got_response.set()
                # everything else (e.g. notifications/initialized): ignore

        async with anyio.create_task_group() as tg:
            tg.start_soon(_stub_peer)
            await client.connect(StdioConfig(command="stub-peer"))
            await swrite.send(
                SessionMessage(
                    JSONRPCMessage(
                        root=JSONRPCRequest(
                            jsonrpc="2.0",
                            id=42,
                            method="sampling/createMessage",
                            params={
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": {"type": "text", "text": "hi"},
                                    }
                                ],
                                "maxTokens": 10,
                            },
                        )
                    )
                )
            )
            with anyio.fail_after(5):
                await got_response.wait()
            await client.aclose()
            tg.cancel_scope.cancel()

    response = captured[0].root
    assert isinstance(response, JSONRPCResponse | JSONRPCError)
    assert response.error is not None
    # SDK 1.30 answers sampling/createMessage via its (unset) sampling
    # callback, which rejects with -32600 invalid-request rather than
    # -32601 method-not-found. The seam this test pins: inbound requests
    # get a JSON-RPC error response, never silence (design decision 4).
    assert response.error.code == -32600


# ---------------------------------------------------------------------------
# Stub-peer harness for exit-detection tests. The peer answers ONLY the
# initialize handshake; everything else (pings, tool calls) goes unanswered,
# and tests kill the connection by closing the peer's send stream or pushing
# an Exception item — exactly what SDK stdio_client emits on subprocess
# death. Mirrors the standalone stub in test_inbound_request_rejected…
# ---------------------------------------------------------------------------


async def _answer_initialize_only(
    sread: MemoryObjectReceiveStream[SessionMessage | Exception],
    swrite: MemoryObjectSendStream[SessionMessage],
) -> None:
    """Minimal peer: respond to initialize, ignore everything else."""
    async for message in sread:
        root = message.message.root
        if isinstance(root, JSONRPCRequest) and root.method == "initialize":
            await swrite.send(
                SessionMessage(
                    JSONRPCMessage(
                        root=JSONRPCResponse(
                            jsonrpc="2.0", id=root.id, result=_INIT_RESULT
                        )
                    )
                )
            )


@asynccontextmanager
async def _stub_harness(
    *, request_timeout: float = 30.0
) -> AsyncIterator[
    tuple[McpClient, MemoryObjectSendStream[SessionMessage | Exception]]
]:
    """Client connected to the stub peer. Yields (client, peer_send_stream)."""
    async with create_client_server_memory_streams() as (
        (cread, cwrite),
        (sread, swrite),
    ):

        @asynccontextmanager
        async def _factory(_config: ServerConfig) -> AsyncIterator[TransportStreams]:
            yield TransportStreams(read=cread, write=cwrite)

        client = McpClient(
            transport_factory=_factory,
            settings=McpSettings(request_timeout_seconds=request_timeout),
        )
        async with anyio.create_task_group() as tg:
            tg.start_soon(_answer_initialize_only, sread, swrite)
            await client.connect(StdioConfig(command="stub-peer"))
            try:
                yield client, swrite
            finally:
                await client.aclose()
                tg.cancel_scope.cancel()


async def test_is_connected_tracks_lifecycle() -> None:
    assert McpClient().is_connected is False
    async with _stub_harness() as (client, _swrite):
        assert client.is_connected is True
    assert client.is_connected is False


async def test_stream_close_marks_disconnected() -> None:
    async with _stub_harness() as (client, swrite):
        # EOF — what a dead subprocess pipe looks like to the reader.
        await swrite.aclose()
        with anyio.fail_after(2):
            while client.is_connected:
                await anyio.sleep(0.01)
        assert client.is_connected is False


async def test_clean_aclose_is_not_logged_as_death(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with _stub_harness():
        pass  # harness calls aclose() — intentional close, not death
    assert "connection lost" not in caplog.text


async def test_death_via_exception_item_fails_calls_with_connection_error() -> None:
    async with _stub_harness() as (client, swrite):
        # SDK stdio_client forwards subprocess failures as Exception items
        # on the read stream — the reason its type is SessionMessage | Exception.
        await swrite.send(RuntimeError("server process exited unexpectedly"))
        with anyio.fail_after(2):
            while client.is_connected:
                await anyio.sleep(0.01)
        with pytest.raises(McpConnectionError):
            await client.ping()
        with pytest.raises(McpConnectionError):
            await client.list_tools()


async def test_calls_after_stream_close_raise_connection_error() -> None:
    async with _stub_harness() as (client, swrite):
        await swrite.aclose()
        with anyio.fail_after(2):
            while client.is_connected:
                await anyio.sleep(0.01)
        with pytest.raises(McpConnectionError):
            await client.list_tools()


async def test_inflight_request_fails_fast_when_server_dies() -> None:
    async with _stub_harness(request_timeout=30.0) as (client, swrite):
        error: BaseException | None = None

        async def _call() -> None:
            nonlocal error
            try:
                await client.ping()  # stub peer never answers pings
            except BaseException as exc:  # noqa: E722 — asserted below
                error = exc

        async def _die_after_a_moment() -> None:
            await anyio.sleep(0.05)
            await swrite.aclose()  # subprocess death mid-request

        with anyio.move_on_after(5) as watchdog:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_call)
                tg.start_soon(_die_after_a_moment)

        # Death must abort the in-flight call in milliseconds, not wait for
        # the 30 s request timeout: the watchdog must NOT have fired.
        assert watchdog.cancelled_caught is False
        assert isinstance(error, McpConnectionError)


async def test_death_during_initialize_raises_connection_error() -> None:
    @asynccontextmanager
    async def _dead_on_arrival(
        _config: ServerConfig,
    ) -> AsyncIterator[TransportStreams]:
        # The "subprocess" dies before answering initialize: the peer's send
        # stream is closed immediately, so the first read hits EOF.
        async with create_client_server_memory_streams() as (
            (cread, cwrite),
            (_sread, swrite),
        ):
            await swrite.aclose()
            yield TransportStreams(read=cread, write=cwrite)

    client = McpClient(transport_factory=_dead_on_arrival)
    with anyio.move_on_after(5) as watchdog:
        with pytest.raises(McpConnectionError):
            await client.connect(StdioConfig(command="dead-on-arrival"))
    assert watchdog.cancelled_caught is False  # died fast, not a 30 s timeout
    assert client.is_connected is False


async def test_external_cancellation_is_not_converted() -> None:
    async with _stub_harness() as (client, _swrite):
        with anyio.move_on_after(0.5) as scope:
            await client.ping()  # never answered; the caller cancels
        # Genuine caller cancellation must propagate as cancellation, not be
        # swallowed and converted to McpConnectionError.
        assert scope.cancelled_caught is True


@asynccontextmanager
async def _reconnectable_harness() -> AsyncIterator[tuple[McpClient, list[int]]]:
    """Client whose factory spawns a fresh in-process server per call.

    Mirrors what restart() does against a real subprocess: every factory
    call is a fresh spawn with its own server, streams, and handshake.
    Yields (client, spawn-log).
    """
    spawns: list[int] = []

    @asynccontextmanager
    async def _factory(_config: ServerConfig) -> AsyncIterator[TransportStreams]:
        spawns.append(len(spawns))
        server = build_server()
        async with create_client_server_memory_streams() as (
            (cread, cwrite),
            (sread, swrite),
        ):
            async with anyio.create_task_group() as tg:
                tg.start_soon(
                    server.run, sread, swrite, server.create_initialization_options()
                )
                try:
                    yield TransportStreams(read=cread, write=cwrite)
                finally:
                    tg.cancel_scope.cancel()

    client = McpClient(transport_factory=_factory)
    await client.connect(StdioConfig(command="in-process"))
    try:
        yield client, spawns
    finally:
        await client.aclose()


async def test_restart_reconnects_from_stored_config() -> None:
    async with _reconnectable_harness() as (client, spawns):
        assert spawns == [0]
        assert client.is_connected is True
        await client.restart()
        assert client.is_connected is True
        assert spawns == [0, 1]  # a fresh spawn ran
        tools = await client.list_tools()
        assert [tool.name for tool in tools] == ["echo", "slow", "tool_error"]
    assert client.is_connected is False


async def test_restart_respawn_failure_keeps_client_dead_and_retryable() -> None:
    attempts: list[int] = []

    @asynccontextmanager
    async def _flaky_factory(
        _config: ServerConfig,
    ) -> AsyncIterator[TransportStreams]:
        attempts.append(1)
        if len(attempts) == 2:
            # The binary "went away" between restarts.
            raise FileNotFoundError(2, "No such file or directory")
        async with create_client_server_memory_streams() as (
            (cread, cwrite),
            (sread, swrite),
        ):
            async with anyio.create_task_group() as tg:
                tg.start_soon(_answer_initialize_only, sread, swrite)
                try:
                    yield TransportStreams(read=cread, write=cwrite)
                finally:
                    tg.cancel_scope.cancel()

    client = McpClient(transport_factory=_flaky_factory)
    await client.connect(StdioConfig(command="flaky"))
    assert client.is_connected is True

    with pytest.raises(McpConnectionError):
        await client.restart()  # second spawn fails
    assert client.is_connected is False
    assert len(attempts) == 2

    await client.restart()  # third spawn succeeds — config retained
    assert client.is_connected is True
    await client.aclose()


async def test_restart_before_connect_raises_not_connected() -> None:
    with pytest.raises(McpNotConnectedError):
        await McpClient().restart()


async def test_on_lost_fires_death_when_server_process_dies(harness: Any) -> None:
    reasons: list[str] = []
    async with harness(on_lost=reasons.append) as (_client, peer):
        await peer.swrite.aclose()  # read stream dies → monitor fires
        await anyio.sleep(0.05)
    assert reasons == ["death"]


async def test_on_lost_fires_timeout_on_request_timeout(harness: Any) -> None:
    reasons: list[str] = []
    async with harness(request_timeout=0.1, on_lost=reasons.append) as (client, _peer):
        with pytest.raises(McpTimeoutError):
            await client.call_tool("slow")  # harness 'slow' sleeps 30s
    assert reasons == ["timeout"]


async def test_on_lost_not_fired_on_success(harness: Any) -> None:
    reasons: list[str] = []
    async with harness(on_lost=reasons.append) as (client, _peer):
        await client.call_tool("echo", {"text": "hi"})
    assert reasons == []


async def test_on_lost_hook_exception_never_reaches_caller(harness: Any) -> None:
    def boom(_reason: str) -> None:
        raise RuntimeError("hook bug")

    async with harness(request_timeout=0.1, on_lost=boom) as (client, _peer):
        with pytest.raises(McpTimeoutError):  # the Octave error, not RuntimeError
            await client.call_tool("slow")


async def test_initialize_timeout_uses_dedicated_budget() -> None:
    """The handshake budget is initialize_timeout_seconds, not the request budget.

    Silent-but-alive peer (never answers initialize); initialize_timeout=0.05,
    request_timeout=1.0. The raised McpTimeoutError must cite 0.05 — proving
    the handshake no longer reads the steady-state knob.
    """

    @asynccontextmanager
    async def _silent_factory(
        _config: ServerConfig,
    ) -> AsyncIterator[TransportStreams]:
        # Memory streams buffer infinitely: the initialize request simply
        # sits unanswered; no peer task needed.
        async with create_client_server_memory_streams() as (
            (cread, cwrite),
            (_sread, _swrite),
        ):
            yield TransportStreams(read=cread, write=cwrite)

    client = McpClient(
        transport_factory=_silent_factory,
        settings=McpSettings(
            initialize_timeout_seconds=0.05, request_timeout_seconds=1.0
        ),
    )
    with pytest.raises(McpTimeoutError) as excinfo:
        await client.connect(StdioConfig(command="silent-peer"))
    assert "0.05" in str(excinfo.value)  # the dedicated budget fired
    assert "1.0" not in str(excinfo.value)  # not the request budget
    assert client.is_connected is False


async def test_slow_initialize_survives_tight_request_timeout() -> None:
    """Regression (#101): a tight request_timeout_seconds must not strangle the
    handshake. Peer boots slowly (answers initialize after 0.2 s) while the
    steady-state budget is 0.05 s; connect() must still succeed on the default
    30 s initialize budget."""

    @asynccontextmanager
    async def _slow_peer_factory(
        _config: ServerConfig,
    ) -> AsyncIterator[TransportStreams]:
        async with create_client_server_memory_streams() as (
            (cread, cwrite),
            (sread, swrite),
        ):

            async def _answer_after_a_moment() -> None:
                message = await sread.receive()
                root = message.message.root
                if isinstance(root, JSONRPCRequest) and root.method == "initialize":
                    await anyio.sleep(0.2)  # slow boot: outlasts request_timeout
                    await swrite.send(
                        SessionMessage(
                            JSONRPCMessage(
                                root=JSONRPCResponse(
                                    jsonrpc="2.0", id=root.id, result=_INIT_RESULT
                                )
                            )
                        )
                    )
                async for _message in sread:
                    pass  # consume notifications/initialized and everything else

            async with anyio.create_task_group() as tg:
                tg.start_soon(_answer_after_a_moment)
                try:
                    yield TransportStreams(read=cread, write=cwrite)
                finally:
                    tg.cancel_scope.cancel()

    client = McpClient(
        transport_factory=_slow_peer_factory,
        settings=McpSettings(request_timeout_seconds=0.05),
    )
    try:
        await client.connect(StdioConfig(command="slow-boot-peer"))
        assert client.is_connected is True
    finally:
        await client.aclose()
