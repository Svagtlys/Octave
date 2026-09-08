"""The adapter contract every inference backend implements."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from octave.inference.config import AdapterConfig
from octave.inference.types import (
    CompletionChunk,
    CompletionRequest,
    CompletionResult,
    EmbeddingRequest,
    EmbeddingResult,
    ModelInfo,
)

__all__ = ["InferenceAdapter"]


class InferenceAdapter(ABC):
    """Contract for inference backends.

    Implementations must be safe for concurrent use within one event loop.
    ``stream`` is a method returning an async iterator (implement as an async
    generator) — never a flag on the request.
    """

    def __init__(self, config: AdapterConfig) -> None:
        self._config = config

    @property
    def config(self) -> AdapterConfig:
        """The configuration this adapter was created with."""
        return self._config

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResult:
        """Run a full chat completion."""

    @abstractmethod
    def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        """Stream a chat completion as deltas; final chunk carries finish_reason."""

    @abstractmethod
    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        """Embed one or more inputs."""

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """List models served by the engine; doubles as the interim health probe."""

    async def aclose(self) -> None:
        """Release resources. Concrete no-op default; adapters override."""
