"""Reusable adapter conformance suite — the anti-over-tailoring enforcement.

Any adapter claiming to satisfy ``InferenceAdapter`` must pass these tests.
"""

import pytest

from octave.inference.adapter import InferenceAdapter
from octave.inference.types import CompletionRequest, EmbeddingRequest, Message

__all__ = ["InferenceAdapterConformanceSuite"]


def _request() -> CompletionRequest:
    return CompletionRequest(
        model=None,
        messages=[Message(role="user", content="ping")],
    )


class InferenceAdapterConformanceSuite:
    """Contract tests every adapter must pass. Provide an ``adapter`` fixture."""

    @pytest.fixture
    def adapter(self) -> InferenceAdapter:
        raise NotImplementedError("subclass must provide an adapter fixture")

    async def test_complete_returns_text(self, adapter: InferenceAdapter) -> None:
        result = await adapter.complete(_request())
        assert result.text

    async def test_complete_reports_model(self, adapter: InferenceAdapter) -> None:
        result = await adapter.complete(_request())
        assert result.model

    async def test_stream_deltas_end_with_finish_reason(
        self, adapter: InferenceAdapter
    ) -> None:
        chunks = [chunk async for chunk in adapter.stream(_request())]
        assert chunks
        assert "".join(chunk.delta_text for chunk in chunks)
        assert chunks[-1].finish_reason is not None

    async def test_embed_one_vector_per_input(self, adapter: InferenceAdapter) -> None:
        result = await adapter.embed(
            EmbeddingRequest(model=None, inputs=["one", "two", "three"])
        )
        assert len(result.embeddings) == 3
        assert all(len(vector) > 0 for vector in result.embeddings)

    async def test_list_models_returns_ids(self, adapter: InferenceAdapter) -> None:
        models = await adapter.list_models()
        assert models
        assert all(model.id for model in models)

    async def test_aclose_is_safe(self, adapter: InferenceAdapter) -> None:
        await adapter.aclose()
