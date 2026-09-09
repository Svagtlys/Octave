"""Pluggable inference adapter layer: contract, registry, built-ins.

Importing this package registers the built-in adapters.
"""

from octave.inference import openai_adapter as _openai_adapter  # noqa: F401
from octave.inference.adapter import InferenceAdapter
from octave.inference.config import AdapterConfig, InferenceSettings
from octave.inference.errors import (
    AdapterAuthError,
    AdapterConnectionError,
    AdapterError,
    AdapterLoadError,
    AdapterRateLimitError,
    AdapterRegistrationError,
    AdapterResponseError,
    ModelNotFoundError,
    UnknownAdapterError,
)
from octave.inference.openai_adapter import OpenAIAdapter
from octave.inference.registry import AdapterRegistry, default_registry, register
from octave.inference.types import (
    CompletionChunk,
    CompletionRequest,
    CompletionResult,
    EmbeddingRequest,
    EmbeddingResult,
    Message,
    ModelInfo,
    Usage,
)

__all__ = [
    "AdapterAuthError",
    "AdapterConfig",
    "AdapterConnectionError",
    "AdapterError",
    "AdapterLoadError",
    "AdapterRateLimitError",
    "AdapterRegistrationError",
    "AdapterRegistry",
    "AdapterResponseError",
    "CompletionChunk",
    "CompletionRequest",
    "CompletionResult",
    "EmbeddingRequest",
    "EmbeddingResult",
    "InferenceAdapter",
    "InferenceSettings",
    "Message",
    "ModelInfo",
    "ModelNotFoundError",
    "OpenAIAdapter",
    "UnknownAdapterError",
    "Usage",
    "default_registry",
    "register",
]
