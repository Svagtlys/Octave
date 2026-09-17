"""Sanity check for the scripted test double used by tool-loop tests."""

import pytest

from octave.inference.config import AdapterConfig
from octave.inference.types import CompletionRequest, CompletionResult, Message
from tests.inference.fakes import ScriptedAdapter


def _config() -> AdapterConfig:
    return AdapterConfig(adapter="scripted", base_url="http://scripted.test/v1")


async def test_scripted_adapter_returns_queued_results_in_order() -> None:
    first = CompletionResult(text="", model="m", finish_reason="tool_calls")
    second = CompletionResult(text="done", model="m", finish_reason="stop")
    adapter = ScriptedAdapter(_config(), responses=[first, second])
    request = CompletionRequest(messages=[Message(role="user", content="hi")])

    assert await adapter.complete(request) is first
    assert await adapter.complete(request) is second


async def test_scripted_adapter_raises_when_exhausted() -> None:
    adapter = ScriptedAdapter(_config(), responses=[])
    request = CompletionRequest(messages=[Message(role="user", content="hi")])
    with pytest.raises(IndexError):
        await adapter.complete(request)
