"""translate_tools: naming, dedupe, routes, ordering (design spec #78)."""

import pytest

from octave.mcp import ServerToolInventory, ToolInfo
from octave.tools import (
    ProviderToolset,
    ToolNameCollisionError,
    ToolRoute,
    translate_tools,
)


def _tool(
    name: str = "read_file",
    description: str | None = None,
    schema: dict | None = None,
) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=description,
        input_schema=schema or {"type": "object", "properties": {}},
    )


def _inventory(
    server_id: str = "srv-1",
    server_name: str = "fs",
    tools: list[ToolInfo] | None = None,
) -> ServerToolInventory:
    return ServerToolInventory(
        server_id=server_id,
        server_name=server_name,
        state="connected",
        tools=tools if tools is not None else [_tool()],
    )


def test_translates_single_tool() -> None:
    result = translate_tools([_inventory()])
    assert isinstance(result, ProviderToolset)
    assert [t.name for t in result.tools] == ["mcp__fs__read_file"]
    assert result.tools[0].parameters == {"type": "object", "properties": {}}
    assert result.routes == {
        "mcp__fs__read_file": ToolRoute(server_id="srv-1", tool_name="read_file")
    }


def test_multi_server_ordering_and_routes() -> None:
    result = translate_tools(
        [
            _inventory("srv-a", "alpha", [_tool("one"), _tool("two")]),
            _inventory("srv-b", "beta", [_tool("three")]),
        ]
    )
    assert [t.name for t in result.tools] == [
        "mcp__alpha__one",
        "mcp__alpha__two",
        "mcp__beta__three",
    ]
    assert result.routes["mcp__beta__three"].server_id == "srv-b"


def test_sanitizes_invalid_characters() -> None:
    result = translate_tools(
        [_inventory(server_name="my.fs:srv", tools=[_tool("get.file!")])]
    )
    assert result.tools[0].name == "mcp__my_fs_srv__get_file_"


def test_truncates_to_64_chars() -> None:
    result = translate_tools(
        [_inventory(server_name="s" * 40, tools=[_tool("t" * 40)])]
    )
    name = result.tools[0].name
    assert len(name) == 64
    assert name == f"mcp__{'s' * 40}__{'t' * 40}"[:64]
    assert list(result.routes) == [name]


def test_collision_raises_naming_both_sources() -> None:
    with pytest.raises(ToolNameCollisionError) as excinfo:
        translate_tools(
            [
                _inventory("srv-a", "fs!", [_tool("read")]),
                _inventory("srv-b", "fs:", [_tool("read")]),
            ]
        )
    message = str(excinfo.value)
    assert "srv-a/read" in message
    assert "srv-b/read" in message


def test_routes_keep_original_unsanitized_tool_name() -> None:
    result = translate_tools(
        [_inventory("srv-9", "srv.name", tools=[_tool("read.file")])]
    )
    route = result.routes["mcp__srv_name__read_file"]
    assert route.server_id == "srv-9"
    assert route.tool_name == "read.file"


def test_empty_inputs_produce_empty_toolset() -> None:
    assert translate_tools([]).tools == []
    assert translate_tools([]).routes == {}
    assert translate_tools([_inventory(tools=[])]).tools == []
