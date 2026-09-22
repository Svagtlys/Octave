"""Minimal stdio MCP server for the integration test.

Spawned by tests/mcp/test_stdio_integration.py via StdioConfig — exercises
the real SDK stdio_client path (subprocess pipes, newline-delimited JSON).
"""

import time

from mcp.server.fastmcp import FastMCP

server = FastMCP("echo-fixture")


@server.tool()
def echo(text: str) -> str:
    """Echo the input text back."""
    return text


@server.tool()
def fail_tool() -> str:
    """Raise — FastMCP converts the exception into an isError=True result."""
    raise RuntimeError("fail_tool exploded")


@server.tool()
def slow_tool() -> str:
    """Block for 5 s — the client-timeout fixture."""
    time.sleep(5)
    return "too late"


if __name__ == "__main__":
    server.run()
