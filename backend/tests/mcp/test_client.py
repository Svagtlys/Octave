"""McpClient lifecycle: handshake, server_info, ping, connect/close guards."""

from typing import Any

import pytest

from octave.mcp.client import McpClient
from octave.mcp.config import StdioConfig
from octave.mcp.errors import McpError, McpNotConnectedError


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
