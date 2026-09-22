"""Public API surface of octave.mcp — the caller-facing vocabulary."""

import octave.mcp as mcp_pkg


def test_public_names_are_exported() -> None:
    for name in (
        "McpClient",
        "McpServerManager",
        "ServerState",
        "ServerStatus",
        "McpSettings",
        "StdioConfig",
        "HttpConfig",
        "ServerInfo",
        "ToolInfo",
        "ToolResult",
        "ToolContent",
        "Notification",
        "McpError",
        "McpRpcError",
        "McpConnectionError",
        "McpNotConnectedError",
        "McpTimeoutError",
        "McpConfigError",
        "get_mcp_manager",
        "ServerToolInventory",
        "ToolRegistry",
        "get_tool_registry",
    ):
        assert hasattr(mcp_pkg, name), name


def test_sdk_types_do_not_leak() -> None:
    # The quarantine rule: no SDK symbols in the package namespace.
    for leaked in ("ClientSession", "StdioServerParameters", "Tool", "CallToolResult"):
        assert not hasattr(mcp_pkg, leaked), leaked
