"""Config validation and transport selection in open_transport()."""

import sys
from typing import Any

import pytest

from octave.mcp.config import HttpConfig, StdioConfig
from octave.mcp.errors import McpConfigError
from octave.mcp.transport import open_transport


@pytest.mark.parametrize(
    "config",
    [
        StdioConfig(command=""),
        HttpConfig(url=""),
        HttpConfig(url="ftp://example/mcp"),
        object(),  # an unknown config type must not reach the SDK
    ],
)
async def test_malformed_config_raises_before_io(config: Any) -> None:
    with pytest.raises(McpConfigError):
        async with open_transport(config):
            pytest.fail("must not yield")


async def test_stdio_transport_opens_and_closes() -> None:
    # A real subprocess that exits immediately: proves the SDK stdio_client
    # spawns under this config and teardown unwinds cleanly.
    config = StdioConfig(command=sys.executable, args=["-c", "pass"])
    async with open_transport(config) as streams:
        assert streams.read is not None
        assert streams.write is not None
