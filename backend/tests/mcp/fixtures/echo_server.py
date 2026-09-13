"""Minimal stdio MCP server for the integration test.

Spawned by tests/mcp/test_stdio_integration.py via StdioConfig — exercises
the real SDK stdio_client path (subprocess pipes, newline-delimited JSON).
"""

from mcp.server.fastmcp import FastMCP

server = FastMCP("echo-fixture")


@server.tool()
def echo(text: str) -> str:
    """Echo the input text back."""
    return text


if __name__ == "__main__":
    server.run()
