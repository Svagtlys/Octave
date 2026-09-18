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
