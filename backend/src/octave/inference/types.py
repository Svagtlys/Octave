"""Octave inference domain types.

These models are the entire vocabulary callers of ``InferenceAdapter`` see.
They deliberately mirror only the subset of the OpenAI dialect shared by local
servers; backend-specific parameters flow through ``extra``.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "CompletionChunk",
    "CompletionRequest",
    "CompletionResult",
    "EmbeddingRequest",
    "EmbeddingResult",
    "Message",
    "ModelInfo",
    "Usage",
]

Role = Literal["system", "user", "assistant"]


class Message(BaseModel):
    """A single chat message."""

    role: Role
    content: str


class Usage(BaseModel):
    """Token accounting for a completion or embedding call."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class CompletionRequest(BaseModel):
    """Request for a chat completion.

    ``model=None`` resolves to the configured default.
    """

    model: str | None = None
    messages: list[Message]
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class CompletionResult(BaseModel):
    """A full (non-streamed) chat completion."""

    text: str
    model: str
    finish_reason: str | None = None
    usage: Usage | None = None


class CompletionChunk(BaseModel):
    """One streamed delta; the final chunk carries ``finish_reason``."""

    delta_text: str = ""
    finish_reason: str | None = None


class EmbeddingRequest(BaseModel):
    """Request for embeddings of one or more inputs."""

    model: str | None = None
    inputs: list[str]
    dimensions: int | None = None


class EmbeddingResult(BaseModel):
    """Embeddings in the same order as the request inputs."""

    embeddings: list[list[float]]
    model: str
    usage: Usage | None = None


class ModelInfo(BaseModel):
    """Minimal model metadata as reported by OpenAI-dialect servers.

    Capability tagging is deliberately out of scope (see spec non-goals).
    """

    id: str
    created: int | None = None
    owned_by: str | None = None
