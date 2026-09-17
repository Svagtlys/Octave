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


def test_message_rejects_unknown_role() -> None:
    with pytest.raises(ValidationError):
        Message(role="function", content="hi")  # type: ignore[arg-type]


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


def test_tool_call_round_trips() -> None:
    call = ToolCall(id="call_1", name="echo", arguments={"text": "hi"})
    assert call.model_dump() == {
        "id": "call_1",
        "name": "echo",
        "arguments": {"text": "hi"},
    }


def test_message_defaults_have_no_tool_fields() -> None:
    message = Message(role="user", content="hi")
    assert message.tool_calls is None
    assert message.tool_call_id is None


def test_assistant_message_can_carry_tool_calls() -> None:
    call = ToolCall(id="call_1", name="echo", arguments={"text": "hi"})
    message = Message(role="assistant", content="", tool_calls=[call])
    assert message.tool_calls == [call]


def test_tool_message_carries_tool_call_id() -> None:
    message = Message(role="tool", content="hi back", tool_call_id="call_1")
    assert message.tool_call_id == "call_1"


def test_completion_result_defaults_tool_calls_to_none() -> None:
    result = CompletionResult(text="hi", model="m")
    assert result.tool_calls is None


def test_completion_result_can_carry_tool_calls() -> None:
    call = ToolCall(id="call_1", name="echo", arguments={})
    result = CompletionResult(text="", model="m", tool_calls=[call])
    assert result.tool_calls == [call]
