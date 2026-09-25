"""ToolLoop: round mechanics, transcript ordering, two-tier error split."""

import pytest

from octave.agent.errors import ToolLoopLimitError
from octave.agent.loop import ToolLoop
from octave.agent.types import ToolOutcome
from octave.inference.errors import AdapterConnectionError
from octave.inference.types import Message
from octave.tools.types import ProviderToolset, ToolRoute
from tests.agent.fakes import (
    RecordingExecutor,
    ScriptedAdapter,
    final_result,
    tool_call,
    tool_result,
)

USER = [Message(role="user", content="hi")]
ROUTE = ToolRoute(server_id="srv", tool_name="read")
TOOLSET = ProviderToolset(tools=[], routes={"mcp__fs__read": ROUTE})


def _loop(
    adapter: ScriptedAdapter, executor: RecordingExecutor, max_rounds: int = 8
) -> ToolLoop:
    return ToolLoop(adapter=adapter, executor=executor, max_tool_rounds=max_rounds)


async def test_passthrough_without_tool_calls() -> None:
    adapter = ScriptedAdapter([final_result("yo")])
    loop = _loop(adapter, RecordingExecutor())
    turn = await loop.run(list(USER), TOOLSET)
    assert turn.tool_rounds == 0
    assert turn.messages == [
        *USER,
        Message(role="assistant", content="yo"),
    ]
    assert turn.result.text == "yo"


async def test_one_round_multiple_calls() -> None:
    calls = [tool_call("c1", "mcp__fs__read"), tool_call("c2", "mcp__fs__read")]
    adapter = ScriptedAdapter([tool_result(*calls), final_result()])
    executor = RecordingExecutor()
    turn = await _loop(adapter, executor).run(list(USER), TOOLSET)
    assert turn.tool_rounds == 1
    assert executor.calls == [("srv", "read", {}), ("srv", "read", {})]
    assert turn.messages == [
        *USER,
        Message(
            role="assistant",
            content="",
            tool_calls=calls,
        ),
        Message(role="tool", content="ok", tool_call_id="c1", name="mcp__fs__read"),
        Message(role="tool", content="ok", tool_call_id="c2", name="mcp__fs__read"),
        Message(role="assistant", content="final"),
    ]


async def test_multi_round_chain() -> None:
    adapter = ScriptedAdapter(
        [
            tool_result(tool_call("c1", "mcp__fs__read")),
            tool_result(tool_call("c2", "mcp__fs__read")),
            final_result(),
        ]
    )
    turn = await _loop(adapter, RecordingExecutor()).run(list(USER), TOOLSET)
    assert turn.tool_rounds == 2
    assert [m.role for m in turn.messages] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant",
    ]


async def test_tools_sent_every_round() -> None:
    from octave.inference.types import ToolDefinition

    populated = ProviderToolset(
        tools=[ToolDefinition(name="mcp__fs__read")], routes={"mcp__fs__read": ROUTE}
    )
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__read")), final_result()]
    )
    await _loop(adapter, RecordingExecutor()).run(list(USER), populated)
    assert [call.tools for call in adapter.complete_calls] == [populated.tools] * 2


async def test_empty_toolset_omits_tools() -> None:
    adapter = ScriptedAdapter([final_result()])
    await _loop(adapter, RecordingExecutor()).run(list(USER), TOOLSET)
    # empty toolset -> tools omitted (None), never an empty list
    assert adapter.complete_calls[0].tools is None


async def test_loop_limit_raises_with_partial_transcript() -> None:
    adapter = ScriptedAdapter([tool_result(tool_call("c1", "mcp__fs__read"))] * 3)
    loop = _loop(adapter, RecordingExecutor(), max_rounds=2)
    with pytest.raises(ToolLoopLimitError) as excinfo:
        await loop.run(list(USER), TOOLSET)
    assert len(adapter.complete_calls) == 3
    # user + (assistant+tool) x 2 executed rounds + unfulfilled assistant
    assert len(excinfo.value.messages) == 1 + 2 * 2 + 1
    assert excinfo.value.messages[-1].role == "assistant"
    assert excinfo.value.messages[-1].tool_calls is not None


async def test_finish_reason_tool_calls_with_empty_list_is_final() -> None:
    from octave.inference.types import CompletionResult

    adapter = ScriptedAdapter(
        [
            CompletionResult(
                text="done", model="fake", finish_reason="tool_calls", tool_calls=[]
            )
        ]
    )
    turn = await _loop(adapter, RecordingExecutor()).run(list(USER), TOOLSET)
    assert turn.tool_rounds == 0
    assert turn.messages[-1].content == "done"


async def test_malformed_arguments_is_error_tool_message() -> None:
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__read", "{not json")), final_result()]
    )
    executor = RecordingExecutor()
    turn = await _loop(adapter, executor).run(list(USER), TOOLSET)
    assert executor.calls == []
    tool_message = turn.messages[2]
    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "c1"
    assert tool_message.content.startswith("Error: ")
    assert "not a valid JSON object" in tool_message.content


async def test_non_object_json_arguments_is_error_tool_message() -> None:
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__read", "[1, 2]")), final_result()]
    )
    turn = await _loop(adapter, RecordingExecutor()).run(list(USER), TOOLSET)
    assert turn.messages[2].content.startswith("Error: ")


async def test_unknown_route_is_error_tool_message() -> None:
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__ghost__x")), final_result()]
    )
    executor = RecordingExecutor()
    turn = await _loop(adapter, executor).run(list(USER), TOOLSET)
    assert executor.calls == []
    assert turn.messages[2].content == "Error: Unknown tool: mcp__ghost__x"


async def test_error_outcome_continues_loop() -> None:
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__read")), final_result()]
    )
    executor = RecordingExecutor([ToolOutcome(content="boom", is_error=True)])
    turn = await _loop(adapter, executor).run(list(USER), TOOLSET)
    assert turn.messages[2].content == "Error: boom"
    assert turn.tool_rounds == 1


async def test_adapter_error_propagates_untouched() -> None:
    adapter = ScriptedAdapter(
        [
            tool_result(tool_call("c1", "mcp__fs__read")),
            AdapterConnectionError("engine down"),
        ]
    )
    with pytest.raises(AdapterConnectionError):
        await _loop(adapter, RecordingExecutor()).run(list(USER), TOOLSET)


async def test_arguments_dict_forwarded_verbatim() -> None:
    adapter = ScriptedAdapter(
        [
            tool_result(tool_call("c1", "mcp__fs__read", '{"path": "/x", "n": 3}')),
            final_result(),
        ]
    )
    executor = RecordingExecutor()
    await _loop(adapter, executor).run(list(USER), TOOLSET)
    assert executor.calls == [("srv", "read", {"path": "/x", "n": 3})]
