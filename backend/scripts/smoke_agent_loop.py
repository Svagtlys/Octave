#!/usr/bin/env python
"""Manual smoke test: run the tool-use orchestration loop end-to-end.

Wires the real seams — a stdio filesystem MCP server through
``ToolRegistry`` -> ``translate_tools`` -> ``ToolLoop`` -> a live
OpenAI-dialect engine — and prompts the model to create a file. Success
criterion: the loop caught at least one tool call and the file exists.

Engine endpoint comes from the usual ``OCTAVE_INFERENCE_*`` env / ``.env``
(see ``InferenceSettings``) unless overridden with flags.

Usage (from backend/):

    # engine from env, temp sandbox, npx filesystem server
    uv run scripts/smoke_agent_loop.py

    # explicit engine / directory / prompt
    uv run scripts/smoke_agent_loop.py \\
        --base-url http://localhost:11434/v1 --model qwen2.5-coder \\
        --dir /tmp/octave-sandbox \\
        --prompt "Create hello.txt containing 'hi'"

Requires Node/npx for the filesystem server. Prints the full transcript.
Exits non-zero when no tool call was caught or the file is missing.
Not part of the test suite.
"""

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from octave.agent import McpToolExecutor, ToolLoop
from octave.inference import (
    AdapterConfig,
    InferenceAdapter,
    InferenceSettings,
    OpenAIAdapter,
)
from octave.inference.errors import AdapterError
from octave.inference.types import Message
from octave.mcp import McpError, McpServerManager, StdioConfig, ToolRegistry
from octave.tools import translate_tools

DEFAULT_PROMPT = (
    "Create a file named note.txt in the working directory containing "
    "exactly the text: hello from octave"
)


async def run(
    *, config: AdapterConfig, directory: Path, prompt: str, max_rounds: int
) -> int:
    """Connect the fleet, run one orchestrated turn, verify the tool call."""
    manager = McpServerManager()
    manager.register(
        id="fs",
        name="filesystem",
        config=StdioConfig(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", str(directory)],
        ),
    )
    registry = ToolRegistry(manager=manager)
    adapter: InferenceAdapter = OpenAIAdapter(config)
    try:
        await manager.start_all()
        await registry.start()
        inventory = await registry.inventory()
        for server in inventory:
            print(f"server:   {server.server_id} — {len(server.tools)} tool(s)")
        toolset = translate_tools(inventory)
        print(f"tools:    {', '.join(tool.name for tool in toolset.tools)}")

        loop = ToolLoop(
            adapter=adapter,
            executor=McpToolExecutor(registry),
            max_tool_rounds=max_rounds,
        )
        turn = await loop.run([Message(role="user", content=prompt)], toolset)

        print(f"\nrounds:   {turn.tool_rounds}")
        print("--- transcript ---")
        tool_call_count = 0
        for message in turn.messages:
            if message.tool_calls:
                tool_call_count += len(message.tool_calls)
                for call in message.tool_calls:
                    print(
                        f"assistant -> tool call: {call.name} "
                        f"arguments={call.arguments}"
                    )
            elif message.role == "tool":
                preview = message.content[:120].replace("\n", " ")
                print(f"tool[{message.name}] {preview}")
            else:
                preview = message.content[:120].replace("\n", " ")
                print(f"{message.role}: {preview}")
        print("--- final ---")
        print(turn.result.text or "(empty)")

        created = directory / "note.txt"
        if turn.tool_rounds == 0 or tool_call_count == 0:
            print(
                "\n✗ no tool call was caught (model answered without tools)",
                file=sys.stderr,
            )
            return 1
        if not created.exists():
            print(
                f"\n✗ tool calls executed but {created} does not exist",
                file=sys.stderr,
            )
            return 1
        print(f"\n✓ caught {tool_call_count} tool call(s); {created} created")
        return 0
    except (McpError, AdapterError) as exc:
        print(f"\n✗ {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await registry.stop()
        await manager.stop_all()
        await adapter.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the tool-use orchestration loop end-to-end."
    )
    parser.add_argument(
        "--base-url", default=None, help="OpenAI-dialect /v1 URL (default: env)"
    )
    parser.add_argument(
        "--model", default=None, help="Model id for completions (default: env)"
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Directory to sandbox the filesystem server (default: temp dir)",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="User message to send")
    parser.add_argument("--max-rounds", type=int, default=8, help="Tool round limit")
    parsed = parser.parse_args()

    directory: Path = parsed.dir or Path(tempfile.mkdtemp(prefix="octave-smoke-loop-"))
    directory.mkdir(parents=True, exist_ok=True)
    print(f"sandbox:  {directory}")

    settings = InferenceSettings()
    config = settings.to_adapter_config()
    if parsed.base_url:
        config = AdapterConfig(
            adapter=config.adapter,
            base_url=parsed.base_url,
            api_key=config.api_key,
            default_model=parsed.model or config.default_model,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
            extra=config.extra,
        )
    elif parsed.model:
        config = AdapterConfig(
            adapter=config.adapter,
            base_url=config.base_url,
            api_key=config.api_key,
            default_model=parsed.model,
            timeout_seconds=config.timeout_seconds,
            max_retries=config.max_retries,
            extra=config.extra,
        )

    sys.exit(
        asyncio.run(
            run(
                config=config,
                directory=directory,
                prompt=parsed.prompt,
                max_rounds=parsed.max_rounds,
            )
        )
    )


if __name__ == "__main__":
    main()
