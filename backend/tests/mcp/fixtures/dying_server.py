"""Stdio MCP server that can kill itself, for exit-capture tests.

Spawned by tests/mcp/test_stdio_integration.py: ``echo`` proves a working
connection, ``exit_now`` terminates the process (os._exit, no cleanup)
mid-request so the test observes exit capture and restart against the
real SDK stdio_client subprocess.
"""

import os

from mcp.server.fastmcp import FastMCP

server = FastMCP("dying-fixture")


@server.tool()
def echo(text: str) -> str:
    """Echo the input text back."""
    return text


@server.tool()
def exit_now() -> str:
    """Terminate the process without answering the request."""
    os._exit(3)


if __name__ == "__main__":
    server.run()
