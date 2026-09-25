"""Domain types are the only vocabulary callers see; verify defaults and validation."""

import pytest
from pydantic import ValidationError

from octave.inference.types import (
    CompletionChunk,
    CompletionRequest,
    CompletionResult,
    EmbeddingRequest,
    Message,
    ModelInfo,
    ToolCall,
    ToolDefinition,
)


def test_completion_request_defaults() -> None:
    request = CompletionRequest(
        model=None, messages=[Message(role="user", content="hi")]
    )
    assert request.model is None
    assert request.temperature is None
    assert request.max_tokens is None
    assert request.top_p is None
    assert request.stop is None
    assert request.extra == {}


def test_completion_request_extra_is_not_shared() -> None:
    a = CompletionRequest(model="m", messages=[])
    b = CompletionRequest(model="m", messages=[])
    a.extra["num_ctx"] = 8192
    assert b.extra == {}


def test_tool_role_is_valid() -> None:
    message = Message(role="tool", content="result", tool_call_id="call_1", name="mcp__fs__read")
    assert message.tool_calls is None


def test_message_tool_fields_default_none() -> None:
    message = Message(role="assistant", content="hi")
    assert message.tool_calls is None
    assert message.tool_call_id is None
    assert message.name is None


def test_message_rejects_truly_unknown_role() -> None:
    with pytest.raises(ValidationError):
        Message(role="developer", content="hi")  # type: ignore[arg-type]


def test_tool_call_round_trip() -> None:
    call = ToolCall(id="call_1", name="mcp__fs__read", arguments='{"path": "/tmp/x"}')
    assert ToolCall.model_validate(call.model_dump()) == call


def test_completion_result_tool_calls_defaults_none() -> None:
    result = CompletionResult(text="hi", model="m")
    assert result.tool_calls is None


def test_completion_result_round_trips_tool_calls() -> None:
    call = ToolCall(id="c1", name="x", arguments="{}")
    result = CompletionResult(text="", model="m", finish_reason="tool_calls", tool_calls=[call])
    assert CompletionResult.model_validate(result.model_dump()) == result


def test_completion_chunk_defaults() -> None:
    chunk = CompletionChunk(delta_text="Hel")
    assert chunk.delta_text == "Hel"
    assert chunk.finish_reason is None


def test_embedding_request_defaults() -> None:
    request = EmbeddingRequest(model=None, inputs=["a", "b"])
    assert request.dimensions is None


def test_model_info_minimal() -> None:
    info = ModelInfo(id="qwen2.5-coder:32b")
    assert info.created is None
    assert info.owned_by is None


def test_completion_request_tools_defaults_none() -> None:
    request = CompletionRequest(model=None, messages=[])
    assert request.tools is None


def test_completion_request_round_trips_tools() -> None:
    tool = ToolDefinition(
        name="mcp__fs__read",
        description="Read a file",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    request = CompletionRequest(model="m", messages=[], tools=[tool])
    assert request.tools == [tool]


def test_tool_definition_defaults() -> None:
    tool = ToolDefinition(name="x")
    assert tool.description is None
    assert tool.parameters == {}
    assert ToolDefinition(name="y").parameters is not tool.parameters
