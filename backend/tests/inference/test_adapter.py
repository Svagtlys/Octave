"""The ABC defines the contract: complete, stream, embed, list_models, aclose."""

from collections.abc import AsyncIterator

import pytest

from octave.inference.adapter import InferenceAdapter
from octave.inference.config import AdapterConfig
from octave.inference.types import (
    CompletionChunk,
    CompletionRequest,
    CompletionResult,
    EmbeddingRequest,
    EmbeddingResult,
    ModelInfo,
)


def _config() -> AdapterConfig:
    return AdapterConfig(adapter="stub", base_url="http://engine.test/v1")


class _Stub(InferenceAdapter):
    """Smallest adapter that satisfies the contract."""

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        return CompletionResult(text="", model="stub")

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        yield CompletionChunk()

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        return EmbeddingResult(embeddings=[], model="stub")

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="stub")]


def test_abc_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        InferenceAdapter(_config())  # type: ignore[abstract]


def test_minimal_subclass_is_instantiable() -> None:
    adapter = _Stub(_config())
    assert adapter.config.adapter == "stub"


async def test_aclose_default_is_a_noop() -> None:
    adapter = _Stub(_config())
    await adapter.aclose()  # must not raise
