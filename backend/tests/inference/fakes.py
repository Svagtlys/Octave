"""Deterministic in-memory adapter — the plugin-shaped test double."""

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
    Usage,
)

__all__ = ["FakeAdapter"]


class FakeAdapter(InferenceAdapter):
    """Serves canned responses; records requests. No network."""

    def __init__(self, config: AdapterConfig) -> None:
        super().__init__(config)
        self.closed = False
        self.complete_calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        self.complete_calls.append(request)
        return CompletionResult(
            text=self._echo(request),
            model=self._model(request.model),
            finish_reason="stop",
            usage=Usage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        )

    async def stream(
        self, request: CompletionRequest
    ) -> AsyncIterator[CompletionChunk]:
        text = self._echo(request)
        for index in range(0, len(text), 2):
            yield CompletionChunk(delta_text=text[index : index + 2])
        yield CompletionChunk(delta_text="", finish_reason="stop")

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        vectors = [[float(len(text)), 0.5] for text in request.inputs]
        count = len(request.inputs)
        return EmbeddingResult(
            embeddings=vectors,
            model=self._model(request.model),
            usage=Usage(prompt_tokens=count, completion_tokens=0, total_tokens=count),
        )

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fake-model", created=1700000000, owned_by="fake")]

    async def aclose(self) -> None:
        self.closed = True

    def _echo(self, request: CompletionRequest) -> str:
        return f"echo:{request.messages[-1].content}"

    def _model(self, requested: str | None) -> str:
        return requested or self.config.default_model or "fake-model"
