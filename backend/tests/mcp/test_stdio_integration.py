"""Real subprocess: StdioConfig → connect → tools → call → close.

Skipped when OCTAVE_MCP_SKIP_SUBPROCESS_TESTS=1 (sandboxes that forbid
spawning subprocesses).
"""

import os
import sys
from pathlib import Path

import pytest

from octave.mcp.client import McpClient
from octave.mcp.config import McpSettings, StdioConfig
from octave.mcp.errors import McpConnectionError

pytestmark = pytest.mark.skipif(
    os.environ.get("OCTAVE_MCP_SKIP_SUBPROCESS_TESTS") == "1",
    reason="subprocess tests opted out via OCTAVE_MCP_SKIP_SUBPROCESS_TESTS",
)

_ECHO_SERVER = Path(__file__).parent / "fixtures" / "echo_server.py"
_DYING_SERVER = Path(__file__).parent / "fixtures" / "dying_server.py"


async def test_stdio_round_trip_against_real_subprocess() -> None:
    client = McpClient()
    try:
        await client.connect(
            StdioConfig(command=sys.executable, args=[str(_ECHO_SERVER)])
        )
        assert client.server_info.name == "echo-fixture"
        assert client.server_info.protocol_version

        tools = await client.list_tools()
        assert [tool.name for tool in tools] == ["echo"]

        result = await client.call_tool("echo", {"text": "hello subprocess"})
        assert result.content[0].text == "hello subprocess"
        assert result.is_error is False
    finally:
        await client.aclose()


async def test_subprocess_exit_is_captured_and_restart_recovers() -> None:
    client = McpClient(settings=McpSettings(request_timeout_seconds=10.0))
    try:
        await client.connect(
            StdioConfig(command=sys.executable, args=[str(_DYING_SERVER)])
        )
        assert client.is_connected is True

        # The server kills itself mid-call: the in-flight request must fail
        # fast with an Octave error, not hang until the 10 s timeout.
        with pytest.raises(McpConnectionError):
            await client.call_tool("exit_now")
        assert client.is_connected is False

        # Subsequent calls fail the same way until a restart.
        with pytest.raises(McpConnectionError):
            await client.ping()

        # Manual restart respawns the subprocess and re-runs the handshake.
        await client.restart()
        assert client.is_connected is True
        result = await client.call_tool("echo", {"text": "reborn"})
        assert result.content[0].text == "reborn"
    finally:
        await client.aclose()


async def test_manager_auto_restarts_real_subprocess() -> None:
    """Death mid-call → supervisor respawns → tool round-trips again."""
    from octave.mcp.config import McpSettings as _Settings
    from octave.mcp.manager import McpServerManager
    from tests.mcp.test_manager import wait_for

    settings = _Settings(
        request_timeout_seconds=10.0,
        restart_base_delay_seconds=0.1,
        restart_max_attempts=3,
    )
    manager = McpServerManager(settings=settings)
    manager.register(
        id="dying",
        name="Dying",
        config=StdioConfig(command=sys.executable, args=[str(_DYING_SERVER)]),
    )
    await manager.start_all()
    try:
        await wait_for(
            manager, "dying", lambda s: s.state == "connected", timeout=15
        )
        client = manager.get_client("dying")
        with pytest.raises(McpConnectionError):
            await client.call_tool("exit_now")
        # Supervisor notices death and auto-restarts (predicate on
        # restart_count so we don't observe the pre-reaction connected).
        await wait_for(
            manager,
            "dying",
            lambda s: s.state == "connected" and s.restart_count >= 1,
            timeout=15,
        )
        result = await client.call_tool("echo", {"text": "auto-reborn"})
        assert result.content[0].text == "auto-reborn"
    finally:
        await manager.stop_all()


async def test_manager_crashes_on_unstartable_server_without_blocking_app() -> None:
    """A bad binary exhausts backoff into 'crashed'; stop_all stays clean."""
    from octave.mcp.config import McpSettings as _Settings
    from octave.mcp.manager import McpServerManager
    from tests.mcp.test_manager import wait_for

    settings = _Settings(
        restart_base_delay_seconds=0.01,
        restart_max_delay_seconds=0.05,
        restart_max_attempts=2,
    )
    manager = McpServerManager(settings=settings)
    manager.register(
        id="bad",
        name="Bad",
        config=StdioConfig(command="/nonexistent/octave-test-binary"),
    )
    await manager.start_all()
    try:
        await wait_for(manager, "bad", lambda s: s.state == "crashed", timeout=15)
        assert manager.status_of("bad").consecutive_failures == 2
    finally:
        await manager.stop_all()
    assert manager.status_of("bad").state == "stopped"
