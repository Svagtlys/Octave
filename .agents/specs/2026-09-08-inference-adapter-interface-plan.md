# Pluggable Inference Adapter Interface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `InferenceAdapter` ABC, an env-backed `AdapterConfig`, a hybrid `AdapterRegistry`, and a conformance-tested `OpenAIAdapter` (via the official `openai` SDK, quarantined to one module) per the approved spec.

**Architecture:** New package `backend/src/octave/inference/` exposing vendor-neutral types, errors, ABC, and registry. Exactly one module (`openai_adapter.py`) may import `openai`; SDK exceptions are translated to Octave errors at that boundary. A reusable pytest conformance suite enforces the contract for built-ins and plugins alike.

**Tech Stack:** Python 3.12, pydantic v2 + pydantic-settings, official `openai` SDK (`AsyncOpenAI`), httpx `MockTransport` for offline tests, pytest (`asyncio_mode = "auto"` — no markers needed), ruff, mypy strict. All commands run from `backend/` unless stated otherwise.

**Spec:** [`.agents/specs/2026-09-08-inference-adapter-interface-design.md`](2026-09-08-inference-adapter-interface-design.md) — approved 2026-09-08.
**Branch:** `feature/inference-adapter-interface` · **Draft PR:** [#73](https://github.com/Svagtlys/Octave/pull/73)

---

## File Map

| Action | File | Purpose |
|--------|------|---------|
| Modify | `pyproject.toml` | Add `openai`, `pydantic-settings` runtime deps |
| Create | `src/octave/inference/__init__.py` | Public re-exports; imports `openai_adapter` so built-ins self-register |
| Create | `src/octave/inference/types.py` | Octave domain types (the only vocabulary callers see) |
| Create | `src/octave/inference/errors.py` | Octave exception hierarchy |
| Create | `src/octave/inference/adapter.py` | `InferenceAdapter` ABC |
| Create | `src/octave/inference/config.py` | `AdapterConfig` dataclass + `InferenceSettings` (env-backed) |
| Create | `src/octave/inference/registry.py` | `@register` decorator, `AdapterRegistry`, import-string resolution |
| Create | `src/octave/inference/openai_adapter.py` | `OpenAIAdapter` — **only module allowed to import `openai`** |
| Create | `tests/inference/__init__.py` | Test package |
| Create | `tests/inference/test_types.py` | Domain type tests |
| Create | `tests/inference/test_errors.py` | Error hierarchy tests |
| Create | `tests/inference/test_adapter.py` | ABC contract tests |
| Create | `tests/inference/test_config.py` | Config/env parsing tests |
| Create | `tests/inference/fakes.py` | `FakeAdapter` — plugin-shaped in-memory adapter |
| Create | `tests/inference/test_registry.py` | Registry resolution/registration tests |
| Create | `tests/inference/conformance.py` | Reusable `InferenceAdapterConformanceSuite` mixin |
| Create | `tests/inference/test_openai_adapter.py` | Conformance subclass over `httpx.MockTransport` + boundary tests |
| Create | `tests/inference/test_package.py` | Public API / self-registration test |
| Modify | `../docs/ARCHITECTURE.md` | Reflect adapter interface + registry |
| Create | `scripts/smoke_inference.py` | Manual smoke script — point at a live engine, ask a question |

Commit messages follow `type(scope): description` ([`.agents/rules/coding.md`](../rules/coding.md)).

---

## Task 1: Dependencies and package skeleton

**Files:**
- Modify: `pyproject.toml:6-9`
- Create: `src/octave/inference/__init__.py`
- Create: `tests/inference/__init__.py`

- [ ] **Step 1: Add runtime dependencies**

In `pyproject.toml`, replace:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.34.0",
]
```

with:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "openai>=2.0.0",
    "pydantic-settings>=2.0.0",
    "uvicorn[standard]>=0.34.0",
]
```

(`httpx` stays dev-only: the `openai` SDK pulls it in at runtime, and tests use `MockTransport` through the injected-client seam.)

- [ ] **Step 2: Install and verify the environment**

Run: `uv sync --extra dev`
Expected: resolves and installs `openai` + `pydantic-settings`; `uv.lock` updated (commit it).

Run: `uv run python -c "import openai, pydantic_settings; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Create the package skeleton**

`src/octave/inference/__init__.py`:

```python
"""Pluggable inference adapter layer (contract + registry)."""
```

`tests/inference/__init__.py`:

```python
"""Tests for the inference adapter layer."""
```

- [ ] **Step 4: Verify nothing broke**

Run: `uv run pytest -q`
Expected: all existing tests pass (`24 passed` or more, no errors).

Run: `uv run ruff check src tests && uv run mypy src`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/octave/inference/__init__.py tests/inference/__init__.py
git commit -m "chore(inference): add openai SDK and pydantic-settings dependencies"
```

---

## Task 2: Domain types (`types.py`)

**Files:**
- Create: `src/octave/inference/types.py`
- Test: `tests/inference/test_types.py`

- [ ] **Step 1: Write the failing tests**

`tests/inference/test_types.py`:

```python
"""Domain types are the only vocabulary callers see; verify defaults and validation."""

import pytest
from pydantic import ValidationError

from octave.inference.types import (
    CompletionChunk,
    CompletionRequest,
    EmbeddingRequest,
    Message,
    ModelInfo,
)


def test_completion_request_defaults() -> None:
    request = CompletionRequest(model=None, messages=[Message(role="user", content="hi")])
    assert request.model is None
    assert request.temperature is None
    assert request.max_tokens is None
    assert request.top_p is None
    assert request.stop is None
    assert request.extra == {}


def test_completion_request_extra_is_not_shared() -> None:
    a = CompletionRequest(model="m", messages=[])
    b = CompletionRequest(model="m", messages=[])
    a.extra["num_ctx"] = 8192
    assert b.extra == {}


def test_message_rejects_unknown_role() -> None:
    with pytest.raises(ValidationError):
        Message(role="tool", content="hi")  # type: ignore[arg-type]


def test_completion_chunk_defaults() -> None:
    chunk = CompletionChunk(delta_text="Hel")
    assert chunk.delta_text == "Hel"
    assert chunk.finish_reason is None


def test_embedding_request_defaults() -> None:
    request = EmbeddingRequest(model=None, inputs=["a", "b"])
    assert request.dimensions is None


def test_model_info_minimal() -> None:
    info = ModelInfo(id="qwen2.5-coder:32b")
    assert info.created is None
    assert info.owned_by is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/inference/test_types.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'octave.inference.types'`

- [ ] **Step 3: Write minimal implementation**

`src/octave/inference/types.py`:

```python
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
    """Request for a chat completion. ``model=None`` resolves to the configured default."""

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/inference/test_types.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add src/octave/inference/types.py tests/inference/test_types.py
git commit -m "feat(inference): add vendor-neutral domain types"
```

---

## Task 3: Error hierarchy (`errors.py`)

**Files:**
- Create: `src/octave/inference/errors.py`
- Test: `tests/inference/test_errors.py`

- [ ] **Step 1: Write the failing tests**

`tests/inference/test_errors.py`:

```python
"""Callers must be able to catch every adapter failure as AdapterError."""

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


def test_every_error_is_an_adapter_error() -> None:
    for cls in (
        AdapterConnectionError,
        AdapterAuthError,
        AdapterRateLimitError,
        ModelNotFoundError,
        AdapterResponseError,
        AdapterLoadError,
        AdapterRegistrationError,
        UnknownAdapterError,
    ):
        assert issubclass(cls, AdapterError)


def test_adapter_response_error_carries_status_code() -> None:
    error = AdapterResponseError("upstream 500", status_code=500)
    assert error.status_code == 500
    assert str(error) == "upstream 500"


def test_unknown_adapter_error_lists_known_names() -> None:
    error = UnknownAdapterError("anthropic", ["fake", "openai"])
    assert error.name == "anthropic"
    assert error.known == ["fake", "openai"]
    assert "anthropic" in str(error)
    assert "fake" in str(error) and "openai" in str(error)


def test_unknown_adapter_error_with_no_registered_names() -> None:
    error = UnknownAdapterError("openai", [])
    assert "no adapters registered" in str(error)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/inference/test_errors.py -v`
Expected: `ModuleNotFoundError: No module named 'octave.inference.errors'`

- [ ] **Step 3: Write minimal implementation**

`src/octave/inference/errors.py`:

```python
"""Octave adapter exception hierarchy.

Wire-layer exceptions (the ``openai`` SDK and below) must never escape an
adapter; they are translated to these types at the adapter boundary.
"""

from collections.abc import Iterable

__all__ = [
    "AdapterAuthError",
    "AdapterConnectionError",
    "AdapterError",
    "AdapterLoadError",
    "AdapterRateLimitError",
    "AdapterRegistrationError",
    "AdapterResponseError",
    "ModelNotFoundError",
    "UnknownAdapterError",
]


class AdapterError(Exception):
    """Base class for every inference adapter failure."""


class AdapterConnectionError(AdapterError):
    """The engine could not be reached (connection refused, DNS, timeout)."""


class AdapterAuthError(AdapterError):
    """The engine rejected our credentials."""


class AdapterRateLimitError(AdapterError):
    """The engine rate-limited us."""


class ModelNotFoundError(AdapterError):
    """The requested model does not exist on the engine."""


class AdapterResponseError(AdapterError):
    """The engine returned an unexpected HTTP status."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class AdapterLoadError(AdapterError):
    """An adapter could not be loaded (bad import string, not a subclass)."""


class AdapterRegistrationError(AdapterError):
    """An adapter name was registered twice (fail fast at import time)."""


class UnknownAdapterError(AdapterError):
    """No adapter matches the requested name."""

    def __init__(self, name: str, known: Iterable[str]) -> None:
        self.name = name
        self.known = sorted(known)
        listed = ", ".join(self.known) if self.known else "no adapters registered"
        super().__init__(f"Unknown adapter {name!r}. Known adapters: {listed}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/inference/test_errors.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add src/octave/inference/errors.py tests/inference/test_errors.py
git commit -m "feat(inference): add octave-side adapter error hierarchy"
```

---

## Task 4: Config (`config.py`)

**Files:**
- Create: `src/octave/inference/config.py`
- Test: `tests/inference/test_config.py`

- [ ] **Step 1: Write the failing tests**

`tests/inference/test_config.py`:

```python
"""Config: frozen AdapterConfig for the registry; env-backed InferenceSettings."""

import dataclasses

import pytest

from octave.inference.config import AdapterConfig, InferenceSettings

_ENV_VARS = [
    "OCTAVE_INFERENCE_ADAPTER",
    "OCTAVE_INFERENCE_BASE_URL",
    "OCTAVE_INFERENCE_API_KEY",
    "OCTAVE_INFERENCE_DEFAULT_MODEL",
    "OCTAVE_INFERENCE_TIMEOUT_SECONDS",
    "OCTAVE_INFERENCE_MAX_RETRIES",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate from the developer's real environment (tests run independently)."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_adapter_config_defaults() -> None:
    config = AdapterConfig(adapter="openai", base_url="http://localhost:11434/v1")
    assert config.api_key == ""
    assert config.default_model is None
    assert config.timeout_seconds == 120.0
    assert config.max_retries == 2
    assert config.extra == {}


def test_adapter_config_extra_not_shared() -> None:
    a = AdapterConfig(adapter="a", base_url="http://x/v1")
    b = AdapterConfig(adapter="b", base_url="http://x/v1")
    a.extra["num_ctx"] = 8192
    assert b.extra == {}


def test_adapter_config_is_frozen() -> None:
    config = AdapterConfig(adapter="openai", base_url="http://x/v1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.base_url = "http://other/v1"  # type: ignore[misc]


def test_settings_defaults_without_env() -> None:
    config = InferenceSettings().to_adapter_config()
    assert config.adapter == "openai"
    assert config.base_url == "http://localhost:11434/v1"  # Ollama default
    assert config.timeout_seconds == 120.0


def test_settings_reads_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTAVE_INFERENCE_ADAPTER", "my_pkg.adapters:MyAdapter")
    monkeypatch.setenv("OCTAVE_INFERENCE_BASE_URL", "http://vllm.test:8000/v1")
    monkeypatch.setenv("OCTAVE_INFERENCE_API_KEY", "secret")
    monkeypatch.setenv("OCTAVE_INFERENCE_DEFAULT_MODEL", "qwen2.5-coder")
    monkeypatch.setenv("OCTAVE_INFERENCE_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("OCTAVE_INFERENCE_MAX_RETRIES", "0")
    config = InferenceSettings().to_adapter_config()
    assert config.adapter == "my_pkg.adapters:MyAdapter"
    assert config.base_url == "http://vllm.test:8000/v1"
    assert config.api_key == "secret"
    assert config.default_model == "qwen2.5-coder"
    assert config.timeout_seconds == 30.0
    assert config.max_retries == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/inference/test_config.py -v`
Expected: `ModuleNotFoundError: No module named 'octave.inference.config'`

- [ ] **Step 3: Write minimal implementation**

`src/octave/inference/config.py`:

```python
"""Adapter configuration.

Two layers: ``AdapterConfig`` is what the registry and adapters consume —
populated from the environment today, from the unified DB at the
composition root later. ``api_key`` is a secret: never log it (redact ``***``).
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["AdapterConfig", "InferenceSettings"]


@dataclass(frozen=True)
class AdapterConfig:
    """Everything an adapter needs to connect. Frozen; safe to share."""

    adapter: str
    """Registered adapter name or a ``module.path:ClassName`` import string."""

    base_url: str
    api_key: str = ""
    default_model: str | None = None
    timeout_seconds: float = 120.0
    max_retries: int = 2
    extra: dict[str, Any] = field(default_factory=dict)


class InferenceSettings(BaseSettings):
    """Env-backed bootstrap config (``OCTAVE_INFERENCE_*`` vars / ``.env``)."""

    model_config = SettingsConfigDict(
        env_prefix="OCTAVE_INFERENCE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    adapter: str = "openai"
    base_url: str = "http://localhost:11434/v1"
    api_key: str = ""
    default_model: str | None = None
    timeout_seconds: float = 120.0
    max_retries: int = 2

    def to_adapter_config(self) -> AdapterConfig:
        """Project these settings into the registry-facing config object."""
        return AdapterConfig(
            adapter=self.adapter,
            base_url=self.base_url,
            api_key=self.api_key,
            default_model=self.default_model,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/inference/test_config.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add src/octave/inference/config.py tests/inference/test_config.py
git commit -m "feat(inference): add env-backed adapter config"
```

---

## Task 5: The ABC (`adapter.py`)

**Files:**
- Create: `src/octave/inference/adapter.py`
- Test: `tests/inference/test_adapter.py`

- [ ] **Step 1: Write the failing tests**

`tests/inference/test_adapter.py`:

```python
"""The ABC defines the contract: complete, stream, embed, list_models, aclose."""

from collections.abc import AsyncIterator

import pytest

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


def _config() -> AdapterConfig:
    return AdapterConfig(adapter="stub", base_url="http://engine.test/v1")


class _Stub(InferenceAdapter):
    """Smallest adapter that satisfies the contract."""

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        return CompletionResult(text="", model="stub")

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        yield CompletionChunk()

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        return EmbeddingResult(embeddings=[], model="stub")

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(id="stub")]


def test_abc_cannot_be_instantiated() -> None:
    with pytest.raises(TypeError):
        InferenceAdapter(_config())  # type: ignore[abstract]


def test_minimal_subclass_is_instantiable() -> None:
    adapter = _Stub(_config())
    assert adapter.config.adapter == "stub"


async def test_aclose_default_is_a_noop() -> None:
    adapter = _Stub(_config())
    await adapter.aclose()  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/inference/test_adapter.py -v`
Expected: `ModuleNotFoundError: No module named 'octave.inference.adapter'`

- [ ] **Step 3: Write minimal implementation**

`src/octave/inference/adapter.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/inference/test_adapter.py -v`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add src/octave/inference/adapter.py tests/inference/test_adapter.py
git commit -m "feat(inference): add InferenceAdapter ABC contract"
```

---

## Task 6: Registry, fakes, and the conformance suite

**Files:**
- Create: `tests/inference/fakes.py`
- Create: `tests/inference/conformance.py`
- Create: `src/octave/inference/registry.py`
- Test: `tests/inference/test_registry.py`

- [ ] **Step 1: Create the fake adapter**

`tests/inference/fakes.py`:

```python
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

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
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
```

- [ ] **Step 2: Create the reusable conformance suite**

`tests/inference/conformance.py` — **every adapter, built-in or plugin, must pass this suite.** Subclass it and provide an `adapter` fixture. Happy-path contract lives here; wire-specific error-translation triggers live in each adapter's own test module (only the OpenAI boundary can trigger SDK errors).

```python
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
```

- [ ] **Step 3: Write the failing registry tests**

`tests/inference/test_registry.py`:

```python
"""Registry: name/import-string resolution, registration rules, fake conformance."""

import pytest

from octave.inference.adapter import InferenceAdapter
from octave.inference.config import AdapterConfig
from octave.inference.errors import (
    AdapterLoadError,
    AdapterRegistrationError,
    UnknownAdapterError,
)
from octave.inference.registry import AdapterRegistry, register
from tests.inference.conformance import InferenceAdapterConformanceSuite
from tests.inference.fakes import FakeAdapter


class NotAnAdapter:
    """Module-level decoy for the non-subclass import-string test."""


def _registry_with_fake() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register("fake", FakeAdapter)
    return registry


def test_register_resolve_and_names() -> None:
    registry = _registry_with_fake()
    assert registry.resolve("fake") is FakeAdapter
    assert registry.names() == ["fake"]


def test_duplicate_registration_rejected() -> None:
    registry = _registry_with_fake()
    with pytest.raises(AdapterRegistrationError):
        registry.register("fake", FakeAdapter)


def test_unknown_name_error_lists_known() -> None:
    registry = _registry_with_fake()
    with pytest.raises(UnknownAdapterError) as excinfo:
        registry.resolve("nope")
    assert "fake" in str(excinfo.value)


def test_import_string_resolves_plugin_class() -> None:
    registry = AdapterRegistry()
    assert registry.resolve("tests.inference.fakes:FakeAdapter") is FakeAdapter


def test_import_string_bad_module_raises_load_error() -> None:
    registry = AdapterRegistry()
    with pytest.raises(AdapterLoadError):
        registry.resolve("no.such.module:Cls")


def test_import_string_non_subclass_raises_load_error() -> None:
    registry = AdapterRegistry()
    with pytest.raises(AdapterLoadError):
        registry.resolve("tests.inference.test_registry:NotAnAdapter")


def test_create_instantiates_from_config() -> None:
    registry = _registry_with_fake()
    adapter = registry.create(
        AdapterConfig(adapter="fake", base_url="http://fake.test/v1")
    )
    assert isinstance(adapter, FakeAdapter)


def test_decorator_registers_into_given_registry() -> None:
    registry = AdapterRegistry()

    @register("decorated", registry=registry)
    class Decorated(FakeAdapter):
        """Adapter registered via decorator."""

    assert registry.resolve("decorated") is Decorated


class TestFakeAdapterConformance(InferenceAdapterConformanceSuite):
    """A plugin-shaped adapter satisfies the contract — the bar for third parties."""

    @pytest.fixture
    def adapter(self) -> InferenceAdapter:
        return FakeAdapter(
            AdapterConfig(
                adapter="fake",
                base_url="http://fake.test/v1",
                default_model="fake-model",
            )
        )
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/inference/test_registry.py -v`
Expected: `ModuleNotFoundError: No module named 'octave.inference.registry'`

- [ ] **Step 5: Write minimal implementation**

`src/octave/inference/registry.py`:

```python
"""Adapter registry: adapter name or import string → adapter instance."""

import importlib
import logging
from collections.abc import Callable, Iterable

from octave.inference.adapter import InferenceAdapter
from octave.inference.config import AdapterConfig
from octave.inference.errors import (
    AdapterLoadError,
    AdapterRegistrationError,
    UnknownAdapterError,
)

__all__ = ["AdapterRegistry", "default_registry", "register"]

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """Maps adapter names (or ``module.path:ClassName`` strings) to classes."""

    def __init__(self) -> None:
        self._adapters: dict[str, type[InferenceAdapter]] = {}

    def register(self, name: str, cls: type[InferenceAdapter]) -> None:
        """Register a class under a name. Duplicates fail fast."""
        if name in self._adapters:
            raise AdapterRegistrationError(f"Adapter {name!r} is already registered")
        self._adapters[name] = cls

    def names(self) -> list[str]:
        """Registered adapter names, sorted (for diagnostics / Settings UI)."""
        return sorted(self._adapters)

    def resolve(self, name_or_import_string: str) -> type[InferenceAdapter]:
        """Resolve a registered name, else a ``module.path:ClassName`` plugin."""
        if name_or_import_string in self._adapters:
            return self._adapters[name_or_import_string]
        if ":" in name_or_import_string:
            return self._resolve_import_string(name_or_import_string)
        logger.warning(
            "Unknown adapter %r; known adapters: %s",
            name_or_import_string,
            self.names(),
        )
        raise UnknownAdapterError(name_or_import_string, self._adapters.keys())

    def create(self, config: AdapterConfig) -> InferenceAdapter:
        """Instantiate the adapter named in the config."""
        return self.resolve(config.adapter)(config)

    def _resolve_import_string(self, import_string: str) -> type[InferenceAdapter]:
        module_name, _, class_name = import_string.partition(":")
        try:
            cls: object = getattr(importlib.import_module(module_name), class_name)
        except (ImportError, AttributeError) as exc:
            logger.warning("Failed to load adapter %r: %s", import_string, exc)
            raise AdapterLoadError(
                f"Could not load adapter {import_string!r}"
            ) from exc
        if not (isinstance(cls, type) and issubclass(cls, InferenceAdapter)):
            logger.warning("%r is not an InferenceAdapter subclass", import_string)
            raise AdapterLoadError(
                f"{import_string!r} does not resolve to an InferenceAdapter subclass"
            )
        return cls


default_registry = AdapterRegistry()


def register(
    name: str, *, registry: AdapterRegistry | None = None
) -> Callable[[type[InferenceAdapter]], type[InferenceAdapter]]:
    """Class decorator registering the adapter; defaults to the global registry."""
    target = default_registry if registry is None else registry

    def decorator(cls: type[InferenceAdapter]) -> type[InferenceAdapter]:
        target.register(name, cls)
        return cls

    return decorator
```

(`UnknownAdapterError.known` accepts `Iterable[str]` — `dict.keys()` passes directly; `errors.py` was written accordingly in Task 3.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/inference/test_registry.py -v`
Expected: `14 passed` (8 registry tests + 6 inherited conformance tests on the fake)

- [ ] **Step 7: Commit**

```bash
git add src/octave/inference/registry.py tests/inference/fakes.py tests/inference/conformance.py tests/inference/test_registry.py
git commit -m "feat(inference): add hybrid adapter registry with conformance suite"
```

---

## Task 7: OpenAIAdapter (`openai_adapter.py`)

**Files:**
- Create: `src/octave/inference/openai_adapter.py`
- Test: `tests/inference/test_openai_adapter.py`

Payload fixtures mirror examples from `refs/openapi.yaml` (the `chatcmpl-123` completion and streaming chunk sequence).

- [ ] **Step 1: Write the failing tests**

`tests/inference/test_openai_adapter.py`:

```python
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
        {"id": "fake-model", "object": "model", "created": 1700000000, "owned_by": "fake"},
        {"id": "fake-embed", "object": "model", "created": 1700000000, "owned_by": "fake"},
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
        return httpx.Response(200, json=EMBEDDINGS_RESPONSE)
    if request.url.path == "/v1/models":
        return httpx.Response(200, json=MODELS_RESPONSE)
    return _error(404, "no_route")


def _mock_client(handler: object = None) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler or _handler)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url=BASE_URL)


def _adapter(
    client: httpx.AsyncClient, **overrides: object
) -> OpenAIAdapter:
    config = AdapterConfig(
        adapter="openai",
        base_url=BASE_URL,
        api_key="test-key",
        default_model="fake-model",
        **overrides,
    )
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/inference/test_openai_adapter.py -v`
Expected: `ModuleNotFoundError: No module named 'octave.inference.openai_adapter'`

- [ ] **Step 3: Write minimal implementation**

`src/octave/inference/openai_adapter.py` — **the only module in Octave that imports `openai`:**

```python
"""OpenAI-dialect adapter built on the official SDK.

QUARANTINE RULE: this is the ONLY module in Octave allowed to import
``openai``. SDK exceptions are translated to the Octave hierarchy here; SDK
types never appear in public signatures.
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import openai
from openai import AsyncOpenAI
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

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        kwargs = self._chat_kwargs(request)
        try:
            sdk_stream = await self._client.chat.completions.create(
                **kwargs, stream=True
            )
            async for event in sdk_stream:
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
        kwargs.update(request.extra)
        return kwargs

    def _resolve_model(self, requested: str | None) -> str:
        model = requested or self._config.default_model
        if model is None:
            raise AdapterError("No model requested and no default_model configured")
        return model
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/inference/test_openai_adapter.py -v`
Expected: `14 passed` (6 inherited conformance + 8 boundary tests)

- [ ] **Step 5: Lint and type-check**

Run: `uv run ruff check src tests && uv run mypy src`
Expected: no errors (the SDK is fully typed; keep `--strict` clean).

- [ ] **Step 6: Commit**

```bash
git add src/octave/inference/openai_adapter.py tests/inference/test_openai_adapter.py
git commit -m "feat(inference): add OpenAI-dialect adapter with SDK quarantine"
```

---

## Task 8: Public API and self-registration (`__init__.py`)

**Files:**
- Modify: `src/octave/inference/__init__.py`
- Test: `tests/inference/test_package.py`

- [ ] **Step 1: Write the failing tests**

`tests/inference/test_package.py`:

```python
"""Public API surface and built-in self-registration."""

import octave.inference as inference


def test_builtin_openai_adapter_self_registers() -> None:
    assert "openai" in inference.default_registry.names()


def test_public_names_are_exported() -> None:
    for name in (
        "InferenceAdapter",
        "AdapterRegistry",
        "default_registry",
        "register",
        "AdapterConfig",
        "InferenceSettings",
        "OpenAIAdapter",
        "AdapterError",
        "CompletionRequest",
        "EmbeddingRequest",
        "ModelInfo",
    ):
        assert hasattr(inference, name), name
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/inference/test_package.py -v`
Expected: FAIL — `AttributeError` / missing `default_registry` (the placeholder `__init__.py` exports nothing).

- [ ] **Step 3: Write the package exports**

`src/octave/inference/__init__.py`:

```python
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
```

(The `_openai_adapter` import is what triggers `@register("openai")` at import time.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/inference/test_package.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add src/octave/inference/__init__.py tests/inference/test_package.py
git commit -m "feat(inference): export public API and self-register built-ins"
```

---

## Task 9: Update architecture docs

**Files:**
- Modify: `docs/ARCHITECTURE.md` (Inference Engine Connector section, ~line 35-51)

- [ ] **Step 1: Add the implemented-status note**

In `docs/ARCHITECTURE.md`, immediately after the "Inference Engine Connector" **Key interactions** block (the line `- Requests tool invocations through the **MCP Connector** when the LLM outputs tool calls`), insert:

```markdown
**Implemented — adapter contract (issue #8):** the `octave.inference` package exposes the
`InferenceAdapter` ABC (`complete`, `stream`, `embed`, `list_models`), an `AdapterRegistry`
resolving adapters by name or `module.path:ClassName` import string, env-backed
`AdapterConfig`, and a built-in `OpenAIAdapter` for OpenAI-dialect servers (Ollama, vLLM,
llama.cpp server, LM Studio). The `openai` SDK is quarantined to `openai_adapter.py`; SDK
errors translate to Octave types. All adapters — including third-party plugins — are gated
by a shared conformance test suite. Design:
[`.agents/specs/2026-09-08-inference-adapter-interface-design.md`](../.agents/specs/2026-09-08-inference-adapter-interface-design.md).
```

- [ ] **Step 2: Commit**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs(architecture): record inference adapter interface and registry"
```

---

## Task 10: Full verification

- [ ] **Step 1: Run the entire backend suite**

Run (from `backend/`): `uv run pytest -q`
Expected: all tests pass — existing scaffold tests plus the new `tests/inference/` suite, zero failures, zero errors.

- [ ] **Step 2: Lint and type-check everything**

Run: `uv run ruff check src tests && uv run mypy src`
Expected: no errors. Fix any findings inline (do not silence with ignores unless unavoidable, and note why in the commit message).

- [ ] **Step 3: Verify the quarantine rule**

Run: `grep -rl "^import openai\|^from openai" src/ | grep -v openai_adapter.py`
Expected: empty output — no module other than `openai_adapter.py` imports `openai`.

- [ ] **Step 4: Commit any remaining fixes**

```bash
git add -A
git commit -m "test(inference): final verification passes for adapter interface"
```

(Skip if the working tree is clean.)

---

## Task 11: Live smoke script (manual verification)

Manual, user-driven check: point the adapter layer at a real OpenAI-compatible backend (Ollama, vLLM, llama.cpp server, …) via the same `OCTAVE_INFERENCE_*` env vars the app will use, and ask it a question. This is the one step that exercises the SSE/HTTP path against a real engine instead of `MockTransport`.

**Files:**
- Create: `scripts/smoke_inference.py`

- [ ] **Step 1: Write the script**

`scripts/smoke_inference.py` (lives outside `src/`, so ruff/mypy in Task 10 don't cover it — keep it clean anyway):

```python
#!/usr/bin/env python
"""Manual smoke test: point the inference layer at a live engine.

Usage (from backend/):

    OCTAVE_INFERENCE_BASE_URL=http://localhost:11434/v1 \
    OCTAVE_INFERENCE_DEFAULT_MODEL=qwen2.5-coder \
    uv run scripts/smoke_inference.py "Hello, who are you?"

Reads the same OCTAVE_INFERENCE_* env vars the app uses (via
InferenceSettings), lists models, sends the prompt, prints the reply.
Exits non-zero on any AdapterError. Not part of the test suite.
"""

import argparse
import asyncio
import sys

from octave.inference import (
    AdapterError,
    CompletionRequest,
    InferenceSettings,
    Message,
    default_registry,
)


async def run(prompt: str, *, stream: bool) -> int:
    """Create the configured adapter, probe it, and answer the prompt."""
    settings = InferenceSettings()
    config = settings.to_adapter_config()
    print(f"adapter:  {config.adapter}")
    print(f"base_url: {config.base_url}")

    try:
        adapter = default_registry.create(config)
    except AdapterError as exc:
        print(f"\u2717 {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        models = await adapter.list_models()
        print(f"models:   {', '.join(m.id for m in models) or '(none)'}")
        model = config.default_model or (models[0].id if models else None)
        if model is None:
            print(
                "\u2717 engine serves no models and OCTAVE_INFERENCE_DEFAULT_MODEL is unset",
                file=sys.stderr,
            )
            return 1

        request = CompletionRequest(
            model=model,
            messages=[Message(role="user", content=prompt)],
        )
        print(f"\n\u2192 {prompt}\n")
        if stream:
            async for chunk in adapter.stream(request):
                print(chunk.delta_text, end="", flush=True)
            print("\n\n\u2713 stream finished")
        else:
            result = await adapter.complete(request)
            print(result.text)
            print(f"\n\u2713 finish_reason={result.finish_reason!r} model={result.model!r}")
        return 0
    except AdapterError as exc:
        print(f"\n\u2717 {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await adapter.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Smoke-test the inference adapter layer against a live engine."
    )
    parser.add_argument("prompt", nargs="+", help="The question to ask the model")
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream deltas instead of one full completion",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run(" ".join(args.prompt), stream=args.stream)))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the error path (no backend required)**

Run (from `backend/`):
```bash
OCTAVE_INFERENCE_BASE_URL=http://127.0.0.1:1 uv run scripts/smoke_inference.py "hi"; echo "exit=$?"
```
Expected: prints `adapter:`/`base_url:` lines, then a clean `✗ AdapterConnectionError: ...` on stderr and `exit=1` — no traceback.

- [ ] **Step 3: Ask a real backend a question (manual, user-run)**

Run against a live OpenAI-compatible engine, e.g. Ollama:
```bash
OCTAVE_INFERENCE_BASE_URL=http://localhost:11434/v1 \
OCTAVE_INFERENCE_DEFAULT_MODEL=qwen2.5-coder \
uv run scripts/smoke_inference.py "Say hello in one short sentence."
```
Expected: model list printed, then the model's reply, then `✓ finish_reason=...`. Repeat with `--stream` to verify the SSE path against the real engine. If the user has no backend available, Step 2 is the minimum bar; note it in the PR.

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_inference.py
git commit -m "feat(inference): add live smoke script for manual adapter verification"
```

---

## Plan Self-Review

Checked against the spec after writing:

1. **Spec coverage:** ABC + 4 methods + `aclose` → Task 5. Registry (decorator + import string + duplicate/load/non-subclass rules + WARN logging) → Task 6. `AdapterConfig` + `InferenceSettings` env layer → Task 4. `OpenAIAdapter` (SDK, injected-client seam, extra passthrough, default-model resolution, full exception-translation table) → Task 7. Conformance suite + fake → Task 6; conformance run against both fake and OpenAI adapter → Tasks 6–7. Dependencies → Task 1. Public API/self-registration → Task 8. `docs/ARCHITECTURE.md` → Task 9. No task for non-goals (tool calling, tagging, health/fallback, DB config) — correctly excluded.
2. **Placeholder scan:** no TBD/TODO/"write tests for the above"; every code step contains full code.
3. **Type consistency:** `AdapterConfig(adapter, base_url, api_key, default_model, timeout_seconds, max_retries, extra)` identical in Tasks 4/5/6/7; `InferenceAdapter.config` property used by `FakeAdapter` (Task 6) matches Task 5; `UnknownAdapterError(name, known: Iterable[str])` defined Task 3, called with `dict.keys()` Task 6; conformance fixture name `adapter` consistent across Tasks 6–7.

One deliberate refinement over the spec's test sketch: error-trigger tests live in `test_openai_adapter.py` rather than the shared suite, because only the OpenAI boundary can trigger SDK-specific failures; the shared suite enforces the behavioral contract that every adapter must satisfy.

4. **Addition beyond spec (user-requested):** Task 11's smoke script gives a repeatable manual check against a real engine — the only path that exercises SSE framing outside `MockTransport`. It is not part of the automated suite and does not change the spec's non-goals (no HTTP routes; app-level chat remains the composition-root work item).
