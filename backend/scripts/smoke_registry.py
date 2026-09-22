#!/usr/bin/env python
"""Manual smoke test: run the ToolRegistry against a live MCP server.

Exercises the full registry lifecycle through the real manager: register
→ start_all → registry.start (warm-up discovery) → inventory → call_tool
(plus --call-fail / --call-timeout for the error and timeout paths) →
registry.stop → stop_all.

Usage (from backend/):

    # echo fixture — warm-up discovery + echo call:
    uv run scripts/smoke_registry.py -- python tests/mcp/fixtures/echo_server.py

    # any stdio server, with a real tool call:
    uv run scripts/smoke_registry.py --call add --args '{"a": 2, "b": 3}' \\
        -- uvx mcp-server-calculator

    # error / timeout paths (echo fixture ships fail_tool + slow_tool):
    uv run scripts/smoke_registry.py --call-fail fail_tool \\
        -- python tests/mcp/fixtures/echo_server.py
    uv run scripts/smoke_registry.py --call-timeout slow_tool --timeout 0.5 \\
        -- python tests/mcp/fixtures/echo_server.py

    # Streamable HTTP server:
    uv run scripts/smoke_registry.py --url http://localhost:8000/mcp

Exits non-zero on unexpected failures. Not part of the test suite.
"""

import argparse
import asyncio
import json
import sys

from octave.mcp import (
    HttpConfig,
    McpError,
    McpTimeoutError,
    ServerConfig,
    StdioConfig,
)
from octave.mcp.config import McpSettings
from octave.mcp.manager import McpServerManager
from octave.mcp.registry import ToolRegistry


async def wait_connected(
    manager: McpServerManager, id: str, timeout: float = 15.0
) -> None:
    """Poll manager state until the server settles (connected or crashed)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        state = manager.status_of(id).state
        if state in ("connected", "crashed"):
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"server {id!r} did not settle within {timeout}s")


async def run(
    config: ServerConfig,
    *,
    timeout: float,
    call: str | None,
    args: dict[str, object] | None,
    call_fail: str | None,
    call_timeout: str | None,
) -> int:
    """Drive the whole registry surface; print what each step reports."""
    settings = McpSettings(request_timeout_seconds=timeout)
    manager = McpServerManager(settings=settings)
    manager.register(id="smoke", name="Smoke", config=config)
    registry = ToolRegistry(manager=manager)
    failures = 0
    await manager.start_all()
    await registry.start()
    try:
        await wait_connected(manager, "smoke")
        status = manager.status_of("smoke")
        print(f"server:    {status.name} [{status.state}] via {status.transport}")

        # Warm-up should already have populated the inventory (connected path).
        [inv] = await registry.inventory()
        print(f"fetched_at: {inv.fetched_at}  last_error: {inv.last_error}")
        for tool in inv.tools:
            print(f"tool:      {tool.name} — {tool.description or '(no description)'}")

        tools = await registry.tools_for("smoke")
        assert [t.name for t in tools] == [t.name for t in inv.tools]
        print("\n✓ tools_for matches warm-up inventory (cache served, no refetch)")

        if call is not None:
            result = await registry.call_tool("smoke", call, args)
            for block in result.content:
                print(
                    f"call:      {call} -> {block.text!r} "
                    f"(is_error={result.is_error})"
                )
            if result.is_error:
                failures += 1

        if call_fail is not None:
            # A JSON-RPC-level failure must surface as McpError, untranslated.
            try:
                result = await registry.call_tool("smoke", call_fail, args)
                print(
                    f"fail:      {call_fail} -> (is_error={result.is_error}) "
                    f"{result.content[0].text if result.content else ''!r}"
                )
            except McpError as exc:
                print(f"fail:      {call_fail} raised {type(exc).__name__}: {exc}")
                print("✓ tool failure propagated as Octave error")

        if call_timeout is not None:
            try:
                await registry.call_tool("smoke", call_timeout, args)
            except McpTimeoutError as exc:
                print(f"✓ timeout: {type(exc).__name__}: {exc}")
            except McpError as exc:
                print(
                    f"? timeout: got {type(exc).__name__} "
                    f"(expected McpTimeoutError): {exc}"
                )
                failures += 1
            else:
                print(f"? timeout: {call_timeout} returned before the timeout fired")
                failures += 1

        # refresh_all with an explicit re-discovery round.
        await registry.refresh_all()
        [inv] = await registry.inventory()
        print(f"✓ refresh_all re-discovered {len(inv.tools)} tool(s)")
        return failures
    finally:
        await registry.stop()
        await manager.stop_all()
        print(f"shutdown:  manager state = {manager.status_of('smoke').state}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the ToolRegistry against a live MCP server."
    )
    parser.add_argument("--url", help="Streamable HTTP endpoint (http:// or https://)")
    parser.add_argument("--timeout", type=float, default=10.0,
                        help="client request timeout in seconds (default 10)")
    parser.add_argument("--call", help="Tool name to invoke (success path)")
    parser.add_argument("--args", help="JSON object of tool arguments")
    parser.add_argument("--call-fail", help="Tool expected to fail (error path)")
    parser.add_argument("--call-timeout", help="Tool expected to exceed --timeout")
    parser.add_argument(
        "command",
        nargs="*",
        help="stdio server command, after `--` (e.g. `-- uvx mcp-server-git`)",
    )
    parsed = parser.parse_args()

    command = [c for c in parsed.command if c != "--"]
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

    try:
        rc = asyncio.run(
            run(
                config,
                timeout=parsed.timeout,
                call=parsed.call,
                args=args,
                call_fail=parsed.call_fail,
                call_timeout=parsed.call_timeout,
            )
        )
    except McpError as exc:
        print(f"\n✗ {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
    sys.exit(1 if rc else 0)


if __name__ == "__main__":
    main()
