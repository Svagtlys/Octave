"""run_tool_loop: reason -> act -> observe over a real in-memory MCP session."""

import pytest

from octave.agent.errors import ToolLoopMaxIterationsError
from octave.agent.tool_loop import run_tool_loop
from octave.inference.config import AdapterConfig
from octave.inference.types import (
    CompletionRequest,
    CompletionResult,
    Message,
    ToolCall,
)
from tests.inference.fakes import ScriptedAdapter
from tests.mcp.conftest import harness_cm


def _scripted(*responses: CompletionResult) -> ScriptedAdapter:
    config = AdapterConfig(adapter="scripted", base_url="http://scripted.test/v1")
    return ScriptedAdapter(config, responses=list(responses))


async def test_single_tool_call_triggers_follow_up_completion() -> None:
    tool_call = ToolCall(id="call_1", name="echo", arguments={"text": "hi"})
    first = CompletionResult(
        text="", model="m", finish_reason="tool_calls", tool_calls=[tool_call]
    )
    second = CompletionResult(text="done", model="m", finish_reason="stop")
    adapter = _scripted(first, second)
    request = CompletionRequest(messages=[Message(role="user", content="say hi")])

    async with harness_cm() as (mcp_client, _peer):
        result = await run_tool_loop(adapter, mcp_client, request)

    assert result is second
    assert len(adapter.complete_calls) == 2
    history = adapter.complete_calls[1].messages
    assert history[0] == Message(role="user", content="say hi")
    assert history[1] == Message(role="assistant", content="", tool_calls=[tool_call])
    assert history[2].role == "tool"
    assert history[2].tool_call_id == "call_1"
    assert history[2].content == "hi"


async def test_multiple_tool_calls_preserve_request_order() -> None:
    calls = [
        ToolCall(id="call_1", name="tool_error", arguments={}),
        ToolCall(id="call_2", name="echo", arguments={"text": "second"}),
    ]
    first = CompletionResult(
        text="", model="m", finish_reason="tool_calls", tool_calls=calls
    )
    second = CompletionResult(text="done", model="m", finish_reason="stop")
    adapter = _scripted(first, second)
    request = CompletionRequest(messages=[Message(role="user", content="go")])

    async with harness_cm() as (mcp_client, _peer):
        result = await run_tool_loop(adapter, mcp_client, request)

    assert result is second
    history = adapter.complete_calls[1].messages
    tool_messages = [m for m in history if m.role == "tool"]
    assert [m.tool_call_id for m in tool_messages] == ["call_1", "call_2"]
    assert tool_messages[0].content == "tool failed"
    assert tool_messages[1].content == "second"


async def test_mcp_level_tool_error_surfaces_as_tool_message() -> None:
    tool_call = ToolCall(id="call_1", name="tool_error", arguments={})
    first = CompletionResult(
        text="", model="m", finish_reason="tool_calls", tool_calls=[tool_call]
    )
    second = CompletionResult(text="done", model="m", finish_reason="stop")
    adapter = _scripted(first, second)
    request = CompletionRequest(messages=[Message(role="user", content="go")])

    async with harness_cm() as (mcp_client, _peer):
        result = await run_tool_loop(adapter, mcp_client, request)

    assert result is second
    tool_message = adapter.complete_calls[1].messages[-1]
    assert tool_message.content == "tool failed"


async def test_unknown_tool_surfaces_rpc_error_as_tool_message() -> None:
    tool_call = ToolCall(id="call_1", name="does_not_exist", arguments={})
    first = CompletionResult(
        text="", model="m", finish_reason="tool_calls", tool_calls=[tool_call]
    )
    second = CompletionResult(text="done", model="m", finish_reason="stop")
    adapter = _scripted(first, second)
    request = CompletionRequest(messages=[Message(role="user", content="go")])

    async with harness_cm() as (mcp_client, _peer):
        result = await run_tool_loop(adapter, mcp_client, request)

    assert result is second
    tool_message = adapter.complete_calls[1].messages[-1]
    assert tool_message.content.startswith("Error:")


async def test_exceeding_max_iterations_raises() -> None:
    tool_call = ToolCall(id="call_1", name="echo", arguments={"text": "hi"})
    always_tool_calls = CompletionResult(
        text="", model="m", finish_reason="tool_calls", tool_calls=[tool_call]
    )
    adapter = _scripted(*([always_tool_calls] * 3))
    request = CompletionRequest(messages=[Message(role="user", content="go")])

    async with harness_cm() as (mcp_client, _peer):
        with pytest.raises(ToolLoopMaxIterationsError) as excinfo:
            await run_tool_loop(adapter, mcp_client, request, max_iterations=3)

    assert excinfo.value.max_iterations == 3
