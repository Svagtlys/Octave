"""Domain types are the only vocabulary callers see; verify defaults and validation."""

import pytest
from pydantic import ValidationError

from octave.inference.types import (
    CompletionChunk,
    CompletionRequest,
    EmbeddingRequest,
    Message,
    ModelInfo,
)


def test_completion_request_defaults() -> None:
    request = CompletionRequest(model=None, messages=[Message(role="user", content="hi")])
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
        Message(role="tool", content="hi")  # type: ignore[arg-type]


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
