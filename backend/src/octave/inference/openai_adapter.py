"""OpenAI-dialect adapter built on the official SDK.

QUARANTINE RULE: this is the ONLY module in Octave allowed to import
``openai``. SDK exceptions are translated to the Octave hierarchy here; SDK
types never appear in public signatures.
"""

import logging
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx
import openai
from openai import AsyncOpenAI, AsyncStream
from openai.types.chat import ChatCompletionChunk
from openai.types.completion_usage import CompletionUsage

from octave.inference.adapter import InferenceAdapter
from octave.inference.config import AdapterConfig
from octave.inference.errors import (
    AdapterAuthError,
    AdapterConnectionError,
    AdapterError,
    AdapterRateLimitError,
    AdapterResponseError,
    ModelNotFoundError,
)
from octave.inference.registry import register
from octave.inference.types import (
    CompletionChunk,
    CompletionRequest,
    CompletionResult,
    EmbeddingRequest,
    EmbeddingResult,
    ModelInfo,
    Usage,
)

__all__ = ["OpenAIAdapter"]

logger = logging.getLogger(__name__)


def _translate(exc: openai.APIError) -> AdapterError:
    """Map an SDK exception onto the Octave hierarchy (boundary rule)."""
    if isinstance(exc, openai.AuthenticationError):
        return AdapterAuthError(str(exc))
    if isinstance(exc, openai.RateLimitError):
        return AdapterRateLimitError(str(exc))
    if isinstance(exc, openai.NotFoundError):
        return ModelNotFoundError(str(exc))
    if isinstance(exc, openai.APIStatusError):
        return AdapterResponseError(str(exc), status_code=exc.status_code)
    if isinstance(exc, openai.APIConnectionError):  # covers APITimeoutError
        return AdapterConnectionError(str(exc))
    return AdapterError(str(exc))


def _chat_usage(usage: CompletionUsage | None) -> Usage | None:
    if usage is None:
        return None
    return Usage(
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )


@register("openai")
class OpenAIAdapter(InferenceAdapter):
    """Adapter for OpenAI-dialect servers (Ollama, vLLM, llama.cpp, LM Studio).

    ``http_client`` is a test seam: inject an ``httpx.AsyncClient`` with
    ``httpx.MockTransport`` to run fully offline.
    """

    def __init__(
        self, config: AdapterConfig, *, http_client: httpx.AsyncClient | None = None
    ) -> None:
        super().__init__(config)
        self._client = AsyncOpenAI(
            base_url=config.base_url,
            api_key=config.api_key or "unused",
            timeout=config.timeout_seconds,
            max_retries=config.max_retries,
            http_client=http_client,
        )

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        kwargs = self._chat_kwargs(request)
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except openai.APIError as exc:
            raise _translate(exc) from exc
        choice = response.choices[0]
        return CompletionResult(
            text=choice.message.content or "",
            model=response.model,
            finish_reason=choice.finish_reason,
            usage=_chat_usage(response.usage),
        )

    async def stream(
        self, request: CompletionRequest
    ) -> AsyncIterator[CompletionChunk]:
        kwargs = self._chat_kwargs(request)
        try:
            sdk_stream = await self._client.chat.completions.create(
                **kwargs, stream=True
            )
            async for event in cast("AsyncStream[ChatCompletionChunk]", sdk_stream):
                if not event.choices:
                    continue
                choice = event.choices[0]
                yield CompletionChunk(
                    delta_text=choice.delta.content or "",
                    finish_reason=choice.finish_reason,
                )
        except openai.APIError as exc:
            raise _translate(exc) from exc

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        kwargs: dict[str, Any] = {
            "model": self._resolve_model(request.model),
            "input": request.inputs,
        }
        if request.dimensions is not None:
            kwargs["dimensions"] = request.dimensions
        try:
            response = await self._client.embeddings.create(**kwargs)
        except openai.APIError as exc:
            raise _translate(exc) from exc
        vectors = [
            item.embedding for item in sorted(response.data, key=lambda d: d.index)
        ]
        usage = None
        if response.usage is not None:
            usage = Usage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=0,
                total_tokens=response.usage.total_tokens,
            )
        return EmbeddingResult(embeddings=vectors, model=response.model, usage=usage)

    async def list_models(self) -> list[ModelInfo]:
        try:
            response = await self._client.models.list()
        except openai.APIError as exc:
            raise _translate(exc) from exc
        return [
            ModelInfo(id=model.id, created=model.created, owned_by=model.owned_by)
            for model in response.data
        ]

    async def aclose(self) -> None:
        await self._client.close()

    def _chat_kwargs(self, request: CompletionRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._resolve_model(request.model),
            "messages": [message.model_dump() for message in request.messages],
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens
        if request.top_p is not None:
            kwargs["top_p"] = request.top_p
        if request.stop is not None:
            kwargs["stop"] = request.stop
        if request.extra:
            kwargs["extra_body"] = dict(request.extra)
        return kwargs

    def _resolve_model(self, requested: str | None) -> str:
        model = requested or self._config.default_model
        if model is None:
            raise AdapterError("No model requested and no default_model configured")
        return model
