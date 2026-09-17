"""The reason -> act -> observe tool-use orchestration loop."""

import anyio

from octave.agent.errors import ToolLoopMaxIterationsError
from octave.inference.adapter import InferenceAdapter
from octave.inference.types import (
    CompletionRequest,
    CompletionResult,
    Message,
    ToolCall,
)
from octave.mcp.client import McpClient
from octave.mcp.errors import McpError

__all__ = ["run_tool_loop"]


async def run_tool_loop(
    adapter: InferenceAdapter,
    mcp_client: McpClient,
    request: CompletionRequest,
    *,
    max_iterations: int = 10,
) -> CompletionResult:
    """Run reason -> act -> observe until the LLM stops requesting tools.

    Each iteration calls ``adapter.complete()``. When the result carries
    ``finish_reason == "tool_calls"``, every requested tool is executed
    concurrently via ``mcp_client.call_tool`` and the results are appended
    to the conversation before the next completion. Returns the first
    result whose ``finish_reason`` is not ``"tool_calls"``.

    Raises ``ToolLoopMaxIterationsError`` if the LLM keeps requesting tools
    past ``max_iterations``.
    """
    messages = list(request.messages)
    for _ in range(max_iterations):
        result = await adapter.complete(
            request.model_copy(update={"messages": messages})
        )
        if result.finish_reason != "tool_calls" or not result.tool_calls:
            return result
        messages.append(
            Message(role="assistant", content=result.text, tool_calls=result.tool_calls)
        )
        messages.extend(await _execute_tool_calls(mcp_client, result.tool_calls))
    raise ToolLoopMaxIterationsError(max_iterations)


async def _execute_tool_calls(
    mcp_client: McpClient, tool_calls: list[ToolCall]
) -> list[Message]:
    results: list[Message | None] = [None] * len(tool_calls)

    async def run_one(index: int, call: ToolCall) -> None:
        results[index] = await _call_and_wrap(mcp_client, call)

    async with anyio.create_task_group() as tg:
        for index, call in enumerate(tool_calls):
            tg.start_soon(run_one, index, call)
    return [message for message in results if message is not None]


async def _call_and_wrap(mcp_client: McpClient, call: ToolCall) -> Message:
    try:
        tool_result = await mcp_client.call_tool(call.name, call.arguments)
    except McpError as exc:
        return Message(role="tool", content=f"Error: {exc}", tool_call_id=call.id)
    content = "".join(block.text for block in tool_result.content)
    return Message(role="tool", content=content, tool_call_id=call.id)
