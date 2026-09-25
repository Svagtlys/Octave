"""Scripted adapter + recording executor for loop tests."""

from collections.abc import AsyncIterator

from octave.agent.types import ToolOutcome
from octave.inference.adapter import InferenceAdapter
from octave.inference.config import AdapterConfig
from octave.inference.types import (
    CompletionChunk,
    CompletionRequest,
    CompletionResult,
    EmbeddingRequest,
    EmbeddingResult,
    ModelInfo,
    ToolCall,
)


class ScriptedAdapter(InferenceAdapter):
    """Serves queued results (Exception items raise); records every request."""

    def __init__(self, results: list[CompletionResult | Exception]) -> None:
        super().__init__(AdapterConfig(adapter="fake", base_url="http://fake.test/v1"))
        self._results = list(results)
        self.complete_calls: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        self.complete_calls.append(request)
        if not self._results:
            raise AssertionError("ScriptedAdapter queue exhausted")
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        raise NotImplementedError
        yield CompletionChunk()  # satisfy async-generator typing

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        raise NotImplementedError

    async def list_models(self) -> list[ModelInfo]:
        return []


def tool_call(call_id: str, name: str, arguments: str = "{}") -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


def tool_result(*calls: ToolCall) -> CompletionResult:
    return CompletionResult(
        text="", model="fake", finish_reason="tool_calls", tool_calls=list(calls)
    )


def final_result(text: str = "final") -> CompletionResult:
    return CompletionResult(text=text, model="fake", finish_reason="stop")


class RecordingExecutor:
    """Returns queued outcomes per call (in order); records invocations."""

    def __init__(self, outcomes: list[ToolOutcome] | None = None) -> None:
        self._outcomes = list(outcomes) if outcomes is not None else []
        self.calls: list[tuple[str, str, dict]] = []

    async def call(
        self, server_id: str, tool_name: str, arguments: dict
    ) -> ToolOutcome:
        self.calls.append((server_id, tool_name, arguments))
        if self._outcomes:
            return self._outcomes.pop(0)
        return ToolOutcome(content="ok")
