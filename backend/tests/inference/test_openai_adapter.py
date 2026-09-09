"""OpenAIAdapter: conformance suite + wire-boundary tests over httpx.MockTransport."""

import json
from collections.abc import AsyncIterator

import httpx
import pytest

from octave.inference.config import AdapterConfig
from octave.inference.errors import (
    AdapterAuthError,
    AdapterConnectionError,
    AdapterError,
    AdapterRateLimitError,
    AdapterResponseError,
    ModelNotFoundError,
)
from octave.inference.openai_adapter import OpenAIAdapter
from octave.inference.types import CompletionRequest, Message
from tests.inference.conformance import InferenceAdapterConformanceSuite

BASE_URL = "http://engine.test/v1"

CHAT_RESPONSE = {
    "id": "chatcmpl-123",
    "object": "chat.completion",
    "created": 1725000000,
    "model": "fake-model",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello from the engine!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 9, "completion_tokens": 12, "total_tokens": 21},
}

STREAM_CHUNKS = [
    {
        "id": "chatcmpl-123",
        "object": "chat.completion.chunk",
        "created": 1725000000,
        "model": "fake-model",
        "choices": [
            {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
        ],
    },
    {
        "id": "chatcmpl-123",
        "object": "chat.completion.chunk",
        "created": 1725000000,
        "model": "fake-model",
        "choices": [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}],
    },
    {
        "id": "chatcmpl-123",
        "object": "chat.completion.chunk",
        "created": 1725000000,
        "model": "fake-model",
        "choices": [
            {"index": 0, "delta": {"content": " there"}, "finish_reason": "stop"}
        ],
    },
]

EMBEDDINGS_RESPONSE = {
    "object": "list",
    "data": [
        {"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]},
        {"object": "embedding", "index": 1, "embedding": [0.4, 0.5, 0.6]},
    ],
    "model": "fake-embed",
    "usage": {"prompt_tokens": 5, "total_tokens": 5},
}

MODELS_RESPONSE = {
    "object": "list",
    "data": [
        {
            "id": "fake-model",
            "object": "model",
            "created": 1700000000,
            "owned_by": "fake",
        },
        {
            "id": "fake-embed",
            "object": "model",
            "created": 1700000000,
            "owned_by": "fake",
        },
    ],
}


def _sse(events: list[dict]) -> str:
    frames = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    return frames + "data: [DONE]\n\n"


def _error(status: int, code: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"error": {"message": f"simulated {code}", "type": code, "code": None}},
    )


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/chat/completions":
        body = json.loads(request.content)
        content = body["messages"][-1]["content"]
        if body.get("stream"):
            return httpx.Response(
                200,
                text=_sse(STREAM_CHUNKS),
                headers={"content-type": "text/event-stream"},
            )
        if "auth" in content:
            return _error(401, "authentication_error")
        if "ratelimit" in content:
            return _error(429, "rate_limit_error")
        if "missing-model" in content:
            return _error(404, "not_found_error")
        if "boom" in content:
            return _error(500, "server_error")
        return httpx.Response(200, json=CHAT_RESPONSE)
    if request.url.path == "/v1/embeddings":
        body = json.loads(request.content)
        inputs = body["input"]
        if isinstance(inputs, str):
            inputs = [inputs]
        payload = {
            "object": "list",
            "data": [
                {"object": "embedding", "index": i, "embedding": [0.1, 0.2, 0.3]}
                for i in range(len(inputs))
            ],
            "model": "fake-embed",
            "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
        }
        return httpx.Response(200, json=payload)
    if request.url.path == "/v1/models":
        return httpx.Response(200, json=MODELS_RESPONSE)
    return _error(404, "no_route")


def _mock_client(handler: object = None) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler or _handler)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url=BASE_URL)


def _adapter(client: httpx.AsyncClient, **overrides: object) -> OpenAIAdapter:
    fields: dict[str, object] = {
        "adapter": "openai",
        "base_url": BASE_URL,
        "api_key": "test-key",
        "default_model": "fake-model",
    }
    fields.update(overrides)
    config = AdapterConfig(**fields)  # type: ignore[arg-type]
    return OpenAIAdapter(config, http_client=client)


async def _expect_failure(content: str, error_type: type[AdapterError]) -> AdapterError:
    client = _mock_client()
    adapter = _adapter(client)
    with pytest.raises(error_type) as excinfo:
        await adapter.complete(
            CompletionRequest(
                model=None, messages=[Message(role="user", content=content)]
            )
        )
    await adapter.aclose()
    return excinfo.value


class TestOpenAIAdapterConformance(InferenceAdapterConformanceSuite):
    """The built-in adapter must pass the same bar as plugins."""

    @pytest.fixture
    async def adapter(self) -> AsyncIterator[OpenAIAdapter]:
        client = _mock_client()
        instance = _adapter(client)
        yield instance
        await instance.aclose()
        await client.aclose()


async def test_sends_bearer_key_and_resolves_default_model() -> None:
    captured: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=CHAT_RESPONSE)

    client = _mock_client(capture)
    adapter = _adapter(client)
    await adapter.complete(
        CompletionRequest(model=None, messages=[Message(role="user", content="hi")])
    )
    assert captured[0].headers["authorization"] == "Bearer test-key"
    assert json.loads(captured[0].content)["model"] == "fake-model"
    await adapter.aclose()
    await client.aclose()


async def test_extra_flows_to_wire_body() -> None:
    captured: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=CHAT_RESPONSE)

    client = _mock_client(capture)
    adapter = _adapter(client)
    request = CompletionRequest(
        model=None,
        messages=[Message(role="user", content="hi")],
        extra={"num_ctx": 8192},
    )
    await adapter.complete(request)
    assert json.loads(captured[0].content)["num_ctx"] == 8192
    await adapter.aclose()
    await client.aclose()


async def test_no_model_and_no_default_raises_adapter_error() -> None:
    client = _mock_client()
    adapter = _adapter(client, default_model=None)
    with pytest.raises(AdapterError):
        await adapter.complete(
            CompletionRequest(model=None, messages=[Message(role="user", content="hi")])
        )
    await adapter.aclose()
    await client.aclose()


async def test_auth_error_translates() -> None:
    await _expect_failure("trigger auth failure", AdapterAuthError)


async def test_rate_limit_translates() -> None:
    await _expect_failure("trigger ratelimit failure", AdapterRateLimitError)


async def test_not_found_translates_to_model_not_found() -> None:
    await _expect_failure("trigger missing-model failure", ModelNotFoundError)


async def test_server_error_carries_status_code() -> None:
    error = await _expect_failure("trigger boom failure", AdapterResponseError)
    assert error.status_code == 500  # type: ignore[attr-defined]


async def test_connection_failure_translates() -> None:
    def raiser(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _mock_client(raiser)
    adapter = _adapter(client)
    with pytest.raises(AdapterConnectionError):
        await adapter.complete(
            CompletionRequest(model=None, messages=[Message(role="user", content="hi")])
        )
    await adapter.aclose()
    await client.aclose()
