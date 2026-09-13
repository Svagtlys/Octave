"""The Octave MCP exception hierarchy — callers only ever catch these."""

from octave.mcp.errors import (
    McpConfigError,
    McpConnectionError,
    McpError,
    McpNotConnectedError,
    McpRpcError,
    McpTimeoutError,
)


def test_all_errors_subclass_base() -> None:
    for cls in (
        McpConnectionError,
        McpNotConnectedError,
        McpTimeoutError,
        McpConfigError,
        McpRpcError,
    ):
        assert issubclass(cls, McpError)


def test_rpc_error_carries_code_and_data() -> None:
    err = McpRpcError("nope", code=-32601, data={"x": 1})
    assert err.code == -32601
    assert err.data == {"x": 1}
    assert str(err) == "nope"


def test_rpc_error_data_defaults_none() -> None:
    assert McpRpcError("boom", code=-32603).data is None
