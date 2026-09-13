"""McpClient lifecycle: handshake, server_info, ping, connect/close guards."""

from typing import Any

import pytest

from octave.mcp.client import McpClient
from octave.mcp.config import StdioConfig
from octave.mcp.errors import (
    McpConnectionError,
    McpError,
    McpNotConnectedError,
    McpRpcError,
)
from octave.mcp.types import ToolContent, ToolResult


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
