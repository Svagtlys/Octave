#!/usr/bin/env python
"""Manual smoke test: point the MCP client core at a live server.

Usage (from backend/):

    # stdio server — everything after `--` is the server command:
    uv run scripts/smoke_mcp.py -- python tests/mcp/fixtures/echo_server.py

    # Streamable HTTP server:
    uv run scripts/smoke_mcp.py --url http://localhost:8000/mcp

    # invoke a tool too:
    uv run scripts/smoke_mcp.py --call echo --args '{"text": "smoke ok"}' \\
        -- python tests/mcp/fixtures/echo_server.py

Connects, prints server info, lists tools; optionally calls one. Exits
non-zero on any McpError. Not part of the test suite.
"""

import argparse
import asyncio
import json
import sys

from octave.mcp import HttpConfig, McpClient, McpError, ServerConfig, StdioConfig


async def run(
    config: ServerConfig, *, call: str | None, args: dict[str, object] | None
) -> int:
    """Connect, probe, list tools, optionally call one; print everything."""
    client = McpClient()
    try:
        await client.connect(config)
        info = client.server_info
        print(
            f"server:   {info.name} {info.version} "
            f"(protocol {info.protocol_version})"
        )
        tools = await client.list_tools()
        for tool in tools:
            print(f"tool:     {tool.name} — {tool.description or '(no description)'}")
        if call is not None:
            if not any(tool.name == call for tool in tools):
                print(
                    f"\u2717 server does not advertise tool {call!r}",
                    file=sys.stderr,
                )
                return 1
            result = await client.call_tool(call, args)
            for block in result.content:
                print(block.text)
            print(f"\n\u2713 is_error={result.is_error}")
        return 0
    except McpError as exc:
        print(f"\n\u2717 {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the MCP client core against a live server."
    )
    parser.add_argument("--url", help="Streamable HTTP endpoint (http:// or https://)")
    parser.add_argument("--call", help="Tool name to invoke after listing")
    parser.add_argument("--args", help="JSON object of tool arguments (with --call)")
    parser.add_argument(
        "command",
        nargs="*",
        help="stdio server command, after `--` (e.g. `-- uvx mcp-server-git`)",
    )
    parsed = parser.parse_args()

    command = [c for c in parsed.command if c != "--"]  # strip the separator
    config: ServerConfig
    if parsed.url:
        config = HttpConfig(url=parsed.url)
    elif command:
        config = StdioConfig(command=command[0], args=command[1:])
    else:
        parser.error("pass --url <endpoint> or `-- <command> [args...]`")

    args: dict[str, object] | None = None
    if parsed.args:
        try:
            args = json.loads(parsed.args)
        except json.JSONDecodeError as exc:
            parser.error(f"--args is not valid JSON: {exc}")

    sys.exit(asyncio.run(run(config, call=parsed.call, args=args)))


if __name__ == "__main__":
    main()
