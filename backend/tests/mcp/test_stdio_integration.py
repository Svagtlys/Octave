"""Real subprocess: StdioConfig → connect → tools → call → close.

Skipped when OCTAVE_MCP_SKIP_SUBPROCESS_TESTS=1 (sandboxes that forbid
spawning subprocesses).
"""

import os
import sys
from pathlib import Path

import pytest

from octave.mcp.client import McpClient
from octave.mcp.config import StdioConfig

pytestmark = pytest.mark.skipif(
    os.environ.get("OCTAVE_MCP_SKIP_SUBPROCESS_TESTS") == "1",
    reason="subprocess tests opted out via OCTAVE_MCP_SKIP_SUBPROCESS_TESTS",
)

_ECHO_SERVER = Path(__file__).parent / "fixtures" / "echo_server.py"


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
