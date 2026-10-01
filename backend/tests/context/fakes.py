"""Deterministic embedding fake for the context plane."""

import hashlib
from collections.abc import AsyncIterator

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


class FakeEmbedAdapter(InferenceAdapter):
    """Hashes each input to a stable vector; records requests.
    complete() raises — the archiver must never generate prose."""

    def __init__(self, dim: int = 4, model: str = "embed-fake") -> None:
        super().__init__(
            AdapterConfig(adapter="fake", base_url="http://fake.test/v1")
        )
        self._dim = dim
        self._model = model
        self.embed_calls: list[EmbeddingRequest] = []

    def vector(self, text: str) -> list[float]:
        digest = hashlib.md5(text.encode()).digest()
        return [digest[i] / 255.0 for i in range(self._dim)]

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        self.embed_calls.append(request)
        model = request.model or self._model
        return EmbeddingResult(
            embeddings=[self.vector(t) for t in request.inputs], model=model
        )

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        raise AssertionError("ContextArchiver must never call complete()")

    async def stream(
        self, request: CompletionRequest
    ) -> AsyncIterator[CompletionChunk]:
        raise NotImplementedError
        yield CompletionChunk()  # satisfy async-generator typing

    async def list_models(self) -> list[ModelInfo]:
        return []
