# Tool-Use Orchestration Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the reason → act → observe loop: detect model tool calls, execute them against MCP servers, feed results back as provider-native tool messages, and re-invoke for final synthesis.

**Architecture:** Additive OpenAI-dialect extensions to `octave.inference` (types + adapter parsing/serialization), then a new composition package `octave.agent` holding the loop (`ToolLoop`), a `ToolExecutor` Protocol, and `McpToolExecutor` wrapping `ToolRegistry`. `octave.tools` stays pure; no SDK imports cross the `octave.agent` boundary; no routes/lifespan wiring (library-only per spec).

**Tech Stack:** Python 3.12, Pydantic v2, anyio, pytest (`asyncio_mode=auto`), `httpx.MockTransport`, uv.

**Spec:** [`.agents/specs/2026-09-25-tool-use-orchestration-loop-design.md`](./2026-09-25-tool-use-orchestration-loop-design.md) — issue #79, branch `feature/tool-use-orchestration-loop`, draft PR [#103](https://github.com/Svagtlys/Octave/pull/103).

**Conventions for all tasks:**
- Work from `backend/` (all paths below relative to repo root; run commands from `backend/`).
- Test command form: `uv run pytest <path> -v`; lint: `uv run ruff check src tests`; types: `uv run mypy src`.
- Commit messages: `type(scope): description`.
- The design doc + this plan are committed first (Task 1) — they are already on disk.
- **Error-content convention (spec clarification):** OpenAI-dialect tool messages carry no error flag; the loop conveys Tier-1 failures by prefixing content with `Error: `. All loop tests assert this shape.

---

### Task 1: Commit design and plan documents

**Files:**
- Commit (already exist): `.agents/specs/2026-09-25-tool-use-orchestration-loop-design.md`, `.agents/specs/2026-09-25-tool-use-orchestration-loop.md`

- [ ] **Step 1: Verify you are on the feature branch**

Run: `git branch --show-current`
Expected: `feature/tool-use-orchestration-loop` (if not: `git checkout feature/tool-use-orchestration-loop`)

- [ ] **Step 2: Commit the docs**

```bash
git add .agents/specs/2026-09-25-tool-use-orchestration-loop-design.md .agents/specs/2026-09-25-tool-use-orchestration-loop.md
git commit -m "docs(specs): add tool-use orchestration loop design and plan"
```

---

### Task 2: Inference domain types — `ToolCall`, tool fields on `Message`, `CompletionResult.tool_calls`

**Files:**
- Modify: `backend/src/octave/inference/types.py`
- Modify: `backend/src/octave/inference/__init__.py`
- Modify: `backend/tests/inference/test_types.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/inference/test_types.py`

Add `ToolCall` and `CompletionResult` to the existing import from `octave.inference.types` (lines 6–13), then append:

```python
def test_tool_role_is_valid() -> None:
    message = Message(role="tool", content="result", tool_call_id="call_1", name="mcp__fs__read")
    assert message.tool_calls is None


def test_message_tool_fields_default_none() -> None:
    message = Message(role="assistant", content="hi")
    assert message.tool_calls is None
    assert message.tool_call_id is None
    assert message.name is None


def test_message_rejects_truly_unknown_role() -> None:
    with pytest.raises(ValidationError):
        Message(role="developer", content="hi")  # type: ignore[arg-type]


def test_tool_call_round_trip() -> None:
    call = ToolCall(id="call_1", name="mcp__fs__read", arguments='{"path": "/tmp/x"}')
    assert ToolCall.model_validate(call.model_dump()) == call


def test_completion_result_tool_calls_defaults_none() -> None:
    result = CompletionResult(text="hi", model="m")
    assert result.tool_calls is None


def test_completion_result_round_trips_tool_calls() -> None:
    call = ToolCall(id="c1", name="x", arguments="{}")
    result = CompletionResult(text="", model="m", finish_reason="tool_calls", tool_calls=[call])
    assert CompletionResult.model_validate(result.model_dump()) == result
```

Also **replace** the existing test at lines 35–37 (`test_message_rejects_unknown_role` — it asserts `role="tool"` raises, which is now wrong):

```python
# DELETE this test entirely (superseded by test_message_rejects_truly_unknown_role
# and test_tool_role_is_valid above):
# def test_message_rejects_unknown_role() -> None:
#     with pytest.raises(ValidationError):
#         Message(role="tool", content="hi")  # type: ignore[arg-type]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/inference/test_types.py -v`
Expected: FAIL — `ImportError: cannot import name 'ToolCall'`

- [ ] **Step 3: Implement the type additions** in `backend/src/octave/inference/types.py`

Replace `Role` (line 24) and `Message` (lines 27–31) and extend `CompletionResult` (lines 70–76):

```python
Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    """One tool call as the model requested it.

    ``arguments`` is the raw JSON string from the wire; Octave never
    interprets it (the adapter stays a lossless translator, the loop parses).
    """

    id: str
    name: str
    arguments: str


class Message(BaseModel):
    """A single chat message.

    ``tool_calls`` populates assistant messages; ``tool_call_id`` and ``name``
    populate ``role="tool"`` result messages (keyed to the call they answer).
    """

    role: Role
    content: str
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
```

```python
class CompletionResult(BaseModel):
    """A full (non-streamed) chat completion."""

    text: str
    model: str
    finish_reason: str | None = None
    usage: Usage | None = None
    tool_calls: list[ToolCall] | None = None
```

Add `"ToolCall"` to `__all__` (lines 12–22, alphabetical position before `"ToolDefinition"`).

In `backend/src/octave/inference/__init__.py`: if `ToolDefinition` is re-exported there (import + `__all__`), add `ToolCall` alongside it in both places; if it is not, skip — types are importable from `octave.inference.types` directly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/inference/test_types.py -v`
Expected: all PASS (including conformance-adjacent tests)

- [ ] **Step 5: Full inference suite still green**

Run: `uv run pytest tests/inference -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/octave/inference/types.py backend/src/octave/inference/__init__.py backend/tests/inference/test_types.py
git commit -m "feat(inference): add tool-call vocabulary to domain types"
```

---

### Task 3: `OpenAIAdapter` — parse `tool_calls` from completion responses

**Files:**
- Modify: `backend/src/octave/inference/openai_adapter.py` (`complete`, lines 89–101)
- Modify: `backend/tests/inference/test_openai_adapter.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/inference/test_openai_adapter.py`

Add `ToolCall` to the import from `octave.inference.types` (line 19). Append:

```python
CHAT_RESPONSE_TOOL_CALLS = {
    "id": "chatcmpl-tc1",
    "object": "chat.completion",
    "created": 1725000000,
    "model": "fake-model",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "mcp__fs__read",
                            "arguments": '{"path": "/tmp/x"}',
                        },
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "mcp__fs__list", "arguments": "{}"},
                    },
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 9, "completion_tokens": 12, "total_tokens": 21},
}


async def test_complete_parses_tool_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=CHAT_RESPONSE_TOOL_CALLS)

    client = _mock_client(handler)
    adapter = _adapter(client)
    result = await adapter.complete(
        CompletionRequest(model=None, messages=[Message(role="user", content="hi")])
    )
    assert result.finish_reason == "tool_calls"
    assert result.text == ""  # None content normalized to ""
    assert result.tool_calls == [
        ToolCall(id="call_1", name="mcp__fs__read", arguments='{"path": "/tmp/x"}'),
        ToolCall(id="call_2", name="mcp__fs__list", arguments="{}"),
    ]
    await adapter.aclose()
    await client.aclose()


async def test_complete_without_tool_calls_is_none() -> None:
    client = _mock_client()
    adapter = _adapter(client)
    result = await adapter.complete(
        CompletionRequest(model=None, messages=[Message(role="user", content="hi")])
    )
    assert result.tool_calls is None
    await adapter.aclose()
    await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/inference/test_openai_adapter.py -k tool_calls -v`
Expected: FAIL — `result.tool_calls` is `None` (field not populated)

- [ ] **Step 3: Implement response parsing** in `backend/src/octave/inference/openai_adapter.py`

Add `ToolCall` to the import from `octave.inference.types` (lines 29–37). Replace `complete` (lines 89–101):

```python
    async def complete(self, request: CompletionRequest) -> CompletionResult:
        kwargs = self._chat_kwargs(request)
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except openai.APIError as exc:
            raise _translate(exc) from exc
        choice = response.choices[0]
        tool_calls = [
            ToolCall(
                id=call.id,
                name=call.function.name,
                arguments=call.function.arguments,
            )
            for call in (choice.message.tool_calls or [])
        ]
        return CompletionResult(
            text=choice.message.content or "",
            model=response.model,
            finish_reason=choice.finish_reason,
            usage=_chat_usage(response.usage),
            tool_calls=tool_calls or None,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/inference/test_openai_adapter.py -k tool_calls -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/inference/openai_adapter.py backend/tests/inference/test_openai_adapter.py
git commit -m "feat(inference): parse tool_calls from completion responses"
```

---

### Task 4: `OpenAIAdapter` — serialize tool-carrying messages to provider payloads

**Files:**
- Modify: `backend/src/octave/inference/openai_adapter.py` (`_chat_kwargs`, lines 158–178)
- Modify: `backend/tests/inference/test_openai_adapter.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/inference/test_openai_adapter.py`

Add `ToolCall` usage (already imported in Task 3). Append:

```python
async def _capture_chat_body(messages: list[Message]) -> dict:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=CHAT_RESPONSE)

    client = _mock_client(handler)
    adapter = _adapter(client)
    await adapter.complete(CompletionRequest(model=None, messages=messages))
    await adapter.aclose()
    await client.aclose()
    return json.loads(captured[0].content)


async def test_plain_messages_emit_no_null_tool_fields() -> None:
    body = await _capture_chat_body(
        [
            Message(role="system", content="sys"),
            Message(role="user", content="hi"),
            Message(role="assistant", content="yo"),
        ]
    )
    for message in body["messages"]:
        assert "tool_calls" not in message
        assert "tool_call_id" not in message
        assert "name" not in message


async def test_assistant_tool_calls_message_uses_provider_envelope() -> None:
    body = await _capture_chat_body(
        [
            Message(role="user", content="hi"),
            Message(
                role="assistant",
                content="",
                tool_calls=[ToolCall(id="call_1", name="mcp__fs__read", arguments='{"p": 1}')],
            ),
        ]
    )
    assert body["messages"][1] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "mcp__fs__read", "arguments": '{"p": 1}'},
            }
        ],
    }


async def test_tool_result_message_carries_tool_call_id_and_name() -> None:
    body = await _capture_chat_body(
        [Message(role="tool", content="42", tool_call_id="call_1", name="mcp__fs__read")]
    )
    assert body["messages"][0] == {
        "role": "tool",
        "content": "42",
        "tool_call_id": "call_1",
        "name": "mcp__fs__read",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/inference/test_openai_adapter.py -k "null_tool_fields or provider_envelope or tool_call_id" -v`
Expected: FAIL — plain messages carry `"tool_calls": null` keys; assistant `tool_calls` serialize flat (no `{"type": "function", "function": ...}` envelope)

- [ ] **Step 3: Implement message serialization** in `backend/src/octave/inference/openai_adapter.py`

In `_chat_kwargs`, replace the `messages` entry (line 161):

```python
            "messages": [self._message_payload(message) for message in request.messages],
```

Add a method after `_chat_kwargs`:

```python
    def _message_payload(self, message: Message) -> dict[str, Any]:
        """One message as a provider payload. exclude_none keeps tool fields
        off plain messages (strict local engines reject null tool keys);
        assistant tool_calls get the provider function-call envelope."""
        payload = message.model_dump(mode="json", exclude_none=True)
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in message.tool_calls
            ]
        return payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/inference/test_openai_adapter.py -v`
Expected: all PASS (existing tools-envelope and extra tests unaffected — `_handler` reads `body["messages"][-1]["content"]`, present on every plain message)

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/inference/openai_adapter.py backend/tests/inference/test_openai_adapter.py
git commit -m "feat(inference): serialize tool-carrying messages to provider payloads"
```

---

### Task 5: `octave.tools.errors` — introduce `ToolError` base

**Files:**
- Modify: `backend/src/octave/tools/errors.py`
- Modify: `backend/tests/tools/test_package.py`

- [ ] **Step 1: Write the failing test** — append to `backend/tests/tools/test_package.py`

Update the import at line 6 usage by adding at top: `from octave.tools.errors import ToolError, ToolNameCollisionError, ToolTranslationError` and append:

```python
def test_tool_error_hierarchy() -> None:
    assert issubclass(ToolTranslationError, ToolError)
    assert issubclass(ToolNameCollisionError, ToolTranslationError)
```

Also add `"ToolError"` to the name tuple in `test_public_names_are_exported` (lines 28–36).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tools/test_package.py -v`
Expected: FAIL — `ImportError: cannot import name 'ToolError'`

- [ ] **Step 3: Implement the restructure** — replace `backend/src/octave/tools/errors.py` entirely

```python
"""Tool-plane errors (design specs #78, #79)."""

__all__ = ["ToolError", "ToolNameCollisionError", "ToolTranslationError"]


class ToolError(Exception):
    """Base for tool-plane failures (translation and orchestration)."""


class ToolTranslationError(ToolError):
    """Base for translation failures."""


class ToolNameCollisionError(ToolTranslationError):
    """Two tools landed on the same exposed name after sanitization."""
```

Add `ToolError` to the import and `__all__` in `backend/src/octave/tools/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/tools -v`
Expected: all PASS (re-base is backward-compatible: existing `except ToolTranslationError` sites catch identically)

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/tools/errors.py backend/src/octave/tools/__init__.py backend/tests/tools/test_package.py
git commit -m "refactor(tools): introduce ToolError base for the tool plane"
```

---

### Task 6: `octave.agent` package skeleton — types, errors, package guard

**Files:**
- Create: `backend/src/octave/agent/__init__.py`
- Create: `backend/src/octave/agent/types.py`
- Create: `backend/src/octave/agent/errors.py`
- Create: `backend/tests/agent/__init__.py` (empty)
- Create: `backend/tests/agent/test_package.py`
- Create: `backend/tests/agent/test_types.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/agent/test_types.py`:

```python
"""Agent loop vocabulary: defaults and provenance of ToolOutcome/ToolTurn."""

from octave.agent.types import ToolOutcome, ToolTurn
from octave.inference.types import CompletionResult, Message


def test_tool_outcome_defaults() -> None:
    outcome = ToolOutcome(content="ok")
    assert outcome.is_error is False


def test_tool_turn_round_trip() -> None:
    turn = ToolTurn(
        messages=[Message(role="user", content="hi"), Message(role="assistant", content="yo")],
        result=CompletionResult(text="yo", model="m"),
        tool_rounds=0,
    )
    assert ToolTurn.model_validate(turn.model_dump()) == turn
```

Create `backend/tests/agent/test_package.py`:

```python
"""Public API surface and SDK-quarantine posture of the agent package."""

import ast
from pathlib import Path

import octave.agent as agent_pkg

SDK_MODULES = {"openai", "mcp"}


def _imported_top_level_modules() -> set[str]:
    names: set[str] = set()
    for path in Path(agent_pkg.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_no_sdk_imports() -> None:
    """octave.agent composes Octave façades only, never the openai/mcp SDKs."""
    assert not _imported_top_level_modules() & SDK_MODULES


def test_public_names_are_exported() -> None:
    for name in (
        "McpToolExecutor",
        "ToolError",
        "ToolExecutor",
        "ToolLoop",
        "ToolLoopLimitError",
        "ToolOutcome",
        "ToolTurn",
    ):
        assert hasattr(agent_pkg, name), name
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agent -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octave.agent'`

- [ ] **Step 3: Implement the package**

Create `backend/src/octave/agent/types.py`:

```python
"""Agent orchestration vocabulary (design spec #79)."""

from pydantic import BaseModel

from octave.inference.types import CompletionResult, Message

__all__ = ["ToolOutcome", "ToolTurn"]


class ToolOutcome(BaseModel):
    """Flattened result of one tool execution."""

    content: str
    is_error: bool = False


class ToolTurn(BaseModel):
    """Result of one orchestrated turn."""

    messages: list[Message]
    """Input + appended assistant/tool messages + final assistant message."""

    result: CompletionResult
    """The final (no tool_calls) completion."""

    tool_rounds: int
    """Tool-execution rounds performed."""
```

Create `backend/src/octave/agent/errors.py`:

```python
"""Orchestration-fatal errors (design spec #79)."""

from octave.inference.types import Message
from octave.tools.errors import ToolError

__all__ = ["ToolLoopLimitError"]


class ToolLoopLimitError(ToolError):
    """max_tool_rounds exhausted without a final answer.

    ``messages`` is the partial transcript (input + everything appended,
    ending on the unfulfilled assistant tool_calls message) so callers can
    persist partial work or retry.
    """

    def __init__(self, message: str, *, messages: list[Message]) -> None:
        super().__init__(message)
        self.messages = messages
```

Create `backend/src/octave/agent/__init__.py` (placeholder for Tasks 7–8; full surface now so the export test is honest — it will fail until Tasks 7–8 land, so temporarily this test is the only red one; run only `test_types.py` here):

```python
"""Agent plane: tool-use orchestration loop (issue #79).

Composition layer — the only package importing both octave.mcp and
octave.inference. Future Agent Manager components (manager, registry,
router) land here as additional modules; they do not exist yet.
"""

from octave.agent.errors import ToolLoopLimitError
from octave.agent.types import ToolOutcome, ToolTurn
from octave.tools.errors import ToolError

__all__ = ["ToolError", "ToolLoopLimitError", "ToolOutcome", "ToolTurn"]
```

- [ ] **Step 4: Run type tests to verify they pass**

Run: `uv run pytest tests/agent/test_types.py -v`
Expected: PASS (`test_package.py::test_public_names_are_exported` is expected-red until Task 8 completes — do not run it green yet)

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/agent backend/tests/agent
git commit -m "feat(agent): scaffold agent package with orchestration vocabulary"
```

---

### Task 7: `ToolLoop` + `ToolExecutor` Protocol

**Files:**
- Create: `backend/src/octave/agent/executor.py`
- Create: `backend/src/octave/agent/loop.py`
- Modify: `backend/src/octave/agent/__init__.py`
- Create: `backend/tests/agent/fakes.py`
- Create: `backend/tests/agent/test_loop.py`

- [ ] **Step 1: Write the fakes** — create `backend/tests/agent/fakes.py`

```python
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
```

- [ ] **Step 2: Write the failing tests** — create `backend/tests/agent/test_loop.py`

```python
"""ToolLoop: round mechanics, transcript ordering, two-tier error split."""

import pytest

from octave.agent.errors import ToolLoopLimitError
from octave.agent.loop import ToolLoop
from octave.agent.types import ToolOutcome
from octave.inference.errors import AdapterConnectionError
from octave.inference.types import Message
from octave.tools.types import ProviderToolset, ToolRoute
from tests.agent.fakes import (
    RecordingExecutor,
    ScriptedAdapter,
    final_result,
    tool_call,
    tool_result,
)

USER = [Message(role="user", content="hi")]
ROUTE = ToolRoute(server_id="srv", tool_name="read")
TOOLSET = ProviderToolset(tools=[], routes={"mcp__fs__read": ROUTE})


def _loop(adapter: ScriptedAdapter, executor: RecordingExecutor, max_rounds: int = 8) -> ToolLoop:
    return ToolLoop(adapter=adapter, executor=executor, max_tool_rounds=max_rounds)


async def test_passthrough_without_tool_calls() -> None:
    adapter = ScriptedAdapter([final_result("yo")])
    loop = _loop(adapter, RecordingExecutor())
    turn = await loop.run(list(USER), TOOLSET)
    assert turn.tool_rounds == 0
    assert turn.messages == [
        *USER,
        Message(role="assistant", content="yo"),
    ]
    assert turn.result.text == "yo"


async def test_one_round_multiple_calls() -> None:
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__read"), tool_call("c2", "mcp__fs__read")), final_result()]
    )
    executor = RecordingExecutor()
    turn = await _loop(adapter, executor).run(list(USER), TOOLSET)
    assert turn.tool_rounds == 1
    assert executor.calls == [("srv", "read", {}), ("srv", "read", {})]
    assert turn.messages == [
        *USER,
        Message(
            role="assistant",
            content="",
            tool_calls=[tool_call("c1", "mcp__fs__read"), tool_call("c2", "mcp__fs__read")],
        ),
        Message(role="tool", content="ok", tool_call_id="c1", name="mcp__fs__read"),
        Message(role="tool", content="ok", tool_call_id="c2", name="mcp__fs__read"),
        Message(role="assistant", content="final"),
    ]


async def test_multi_round_chain() -> None:
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__read")), tool_result(tool_call("c2", "mcp__fs__read")), final_result()]
    )
    turn = await _loop(adapter, RecordingExecutor()).run(list(USER), TOOLSET)
    assert turn.tool_rounds == 2
    assert [m.role for m in turn.messages] == [
        "user", "assistant", "tool", "assistant", "tool", "assistant",
    ]


async def test_tools_sent_every_round() -> None:
    from octave.inference.types import ToolDefinition

    populated = ProviderToolset(
        tools=[ToolDefinition(name="mcp__fs__read")], routes={"mcp__fs__read": ROUTE}
    )
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__read")), final_result()]
    )
    await _loop(adapter, RecordingExecutor()).run(list(USER), populated)
    assert [call.tools for call in adapter.complete_calls] == [populated.tools] * 2


async def test_empty_toolset_omits_tools() -> None:
    adapter = ScriptedAdapter([final_result()])
    await _loop(adapter, RecordingExecutor()).run(list(USER), TOOLSET)
    # empty toolset -> tools omitted (None), never an empty list
    assert adapter.complete_calls[0].tools is None


async def test_loop_limit_raises_with_partial_transcript() -> None:
    adapter = ScriptedAdapter([tool_result(tool_call("c1", "mcp__fs__read"))] * 3)
    loop = _loop(adapter, RecordingExecutor(), max_rounds=2)
    with pytest.raises(ToolLoopLimitError) as excinfo:
        await loop.run(list(USER), TOOLSET)
    assert len(adapter.complete_calls) == 3
    assert len(excinfo.value.messages) == 1 + 2 * 2  # user + (assistant+tool) x 2
    assert excinfo.value.messages[-1].role == "assistant"
    assert excinfo.value.messages[-1].tool_calls is not None


async def test_finish_reason_tool_calls_with_empty_list_is_final() -> None:
    from octave.inference.types import CompletionResult

    adapter = ScriptedAdapter(
        [CompletionResult(text="done", model="fake", finish_reason="tool_calls", tool_calls=[])]
    )
    turn = await _loop(adapter, RecordingExecutor()).run(list(USER), TOOLSET)
    assert turn.tool_rounds == 0
    assert turn.messages[-1].content == "done"


async def test_malformed_arguments_is_error_tool_message() -> None:
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__read", "{not json")), final_result()]
    )
    executor = RecordingExecutor()
    turn = await _loop(adapter, executor).run(list(USER), TOOLSET)
    assert executor.calls == []
    tool_message = turn.messages[2]
    assert tool_message.role == "tool"
    assert tool_message.tool_call_id == "c1"
    assert tool_message.content.startswith("Error: ")
    assert "not a valid JSON object" in tool_message.content


async def test_non_object_json_arguments_is_error_tool_message() -> None:
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__read", "[1, 2]")), final_result()]
    )
    turn = await _loop(adapter, RecordingExecutor()).run(list(USER), TOOLSET)
    assert turn.messages[2].content.startswith("Error: ")


async def test_unknown_route_is_error_tool_message() -> None:
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__ghost__x")), final_result()]
    )
    executor = RecordingExecutor()
    turn = await _loop(adapter, executor).run(list(USER), TOOLSET)
    assert executor.calls == []
    assert turn.messages[2].content == "Error: Unknown tool: mcp__ghost__x"


async def test_error_outcome_continues_loop() -> None:
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__read")), final_result()]
    )
    executor = RecordingExecutor([ToolOutcome(content="boom", is_error=True)])
    turn = await _loop(adapter, executor).run(list(USER), TOOLSET)
    assert turn.messages[2].content == "Error: boom"
    assert turn.tool_rounds == 1


async def test_adapter_error_propagates_untouched() -> None:
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__read")), AdapterConnectionError("engine down")]
    )
    with pytest.raises(AdapterConnectionError):
        await _loop(adapter, RecordingExecutor()).run(list(USER), TOOLSET)


async def test_arguments_dict_forwarded_verbatim() -> None:
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__read", '{"path": "/x", "n": 3}')), final_result()]
    )
    executor = RecordingExecutor()
    await _loop(adapter, executor).run(list(USER), TOOLSET)
    assert executor.calls == [("srv", "read", {"path": "/x", "n": 3})]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/agent/test_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octave.agent.loop'`

- [ ] **Step 4: Implement `executor.py`** — create `backend/src/octave/agent/executor.py`

```python
"""Tool-execution seam (design spec #79 decision 7)."""

from typing import Any, Protocol

from octave.agent.types import ToolOutcome

__all__ = ["ToolExecutor"]


class ToolExecutor(Protocol):
    """Executes one resolved tool call.

    Implementations must not raise for tool-level failures — return
    ``ToolOutcome(is_error=True)`` so the model can self-correct.
    """

    async def call(
        self, server_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> ToolOutcome: ...
```

- [ ] **Step 5: Implement `loop.py`** — create `backend/src/octave/agent/loop.py`

```python
"""The reason → act → observe loop (issue #79).

Composes an InferenceAdapter, a ToolExecutor, and a pre-built
ProviderToolset (#78 routes). Tier-1 tool failures become error tool
messages the model can react to; the round limit and adapter failures
raise (spec decision 3).
"""

import json
import logging
from typing import Any

from octave.agent.errors import ToolLoopLimitError
from octave.agent.executor import ToolExecutor
from octave.agent.types import ToolOutcome, ToolTurn
from octave.inference.adapter import InferenceAdapter
from octave.inference.types import CompletionRequest, Message, ToolCall
from octave.tools.types import ProviderToolset

__all__ = ["ToolLoop"]

logger = logging.getLogger(__name__)

_NO_OUTPUT = "(no output)"


class ToolLoop:
    """Runs one orchestrated turn: completions, tool rounds, final synthesis."""

    def __init__(
        self,
        *,
        adapter: InferenceAdapter,
        executor: ToolExecutor,
        max_tool_rounds: int = 8,
    ) -> None:
        self._adapter = adapter
        self._executor = executor
        self._max_tool_rounds = max_tool_rounds

    async def run(
        self,
        messages: list[Message],
        toolset: ProviderToolset,
        *,
        model: str | None = None,
    ) -> ToolTurn:
        """Drive messages to a final (no tool_calls) completion.

        Raises ToolLoopLimitError if the model still requests tools after
        ``max_tool_rounds`` executions; AdapterError propagates untouched.
        """
        history = list(messages)
        rounds = 0
        tools = toolset.tools or None
        while True:
            result = await self._adapter.complete(
                CompletionRequest(model=model, messages=history, tools=tools)
            )
            if not result.tool_calls:
                history.append(Message(role="assistant", content=result.text))
                return ToolTurn(messages=history, result=result, tool_rounds=rounds)
            history.append(
                Message(
                    role="assistant", content=result.text, tool_calls=result.tool_calls
                )
            )
            if rounds >= self._max_tool_rounds:
                logger.warning(
                    "tool loop limit reached | max_tool_rounds=%s calls=%s",
                    self._max_tool_rounds,
                    len(result.tool_calls),
                )
                raise ToolLoopLimitError(
                    f"tool loop exhausted after {self._max_tool_rounds} rounds",
                    messages=history,
                )
            rounds += 1
            logger.debug("tool round | round=%s calls=%s", rounds, len(result.tool_calls))
            for call in result.tool_calls:
                history.append(await self._execute(call, toolset))

    async def _execute(self, call: ToolCall, toolset: ProviderToolset) -> Message:
        """One call -> one tool message; Tier-1 failures never raise."""
        route = toolset.routes.get(call.name)
        if route is None:
            return self._tool_message(
                call, ToolOutcome(content=f"Unknown tool: {call.name}", is_error=True)
            )
        arguments = self._parse_arguments(call)
        if isinstance(arguments, str):  # parse error message
            return self._tool_message(
                call,
                ToolOutcome(
                    content=f"Tool call arguments are not a valid JSON object: {arguments}",
                    is_error=True,
                ),
            )
        outcome = await self._executor.call(route.server_id, route.tool_name, arguments)
        if outcome.is_error:
            logger.warning(
                "tool call failed | tool=%s server=%s error=%s",
                call.name,
                route.server_id,
                outcome.content,
            )
        return self._tool_message(call, outcome)

    @staticmethod
    def _parse_arguments(call: ToolCall) -> dict[str, Any] | str:
        """Parsed dict, or an error description string."""
        try:
            parsed = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return str(exc)
        if not isinstance(parsed, dict):
            return f"expected an object, got {type(parsed).__name__}"
        return parsed

    @staticmethod
    def _tool_message(call: ToolCall, outcome: ToolOutcome) -> Message:
        content = outcome.content or _NO_OUTPUT
        if outcome.is_error:
            content = f"Error: {content}"
        return Message(
            role="tool", content=content, tool_call_id=call.id, name=call.name
        )
```

Update `backend/src/octave/agent/__init__.py` imports and `__all__` to:

```python
from octave.agent.errors import ToolLoopLimitError
from octave.agent.executor import ToolExecutor
from octave.agent.loop import ToolLoop
from octave.agent.types import ToolOutcome, ToolTurn
from octave.tools.errors import ToolError

__all__ = [
    "ToolError",
    "ToolExecutor",
    "ToolLoop",
    "ToolLoopLimitError",
    "ToolOutcome",
    "ToolTurn",
]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/agent/test_loop.py -v`
Expected: all PASS (13 tests)

- [ ] **Step 7: Commit**

```bash
git add backend/src/octave/agent backend/tests/agent
git commit -m "feat(agent): implement tool-use orchestration loop"
```

---

### Task 8: `McpToolExecutor`

**Files:**
- Create: `backend/src/octave/agent/mcp_executor.py`
- Modify: `backend/src/octave/agent/__init__.py`
- Create: `backend/tests/agent/test_mcp_executor.py`

- [ ] **Step 1: Write the failing tests** — create `backend/tests/agent/test_mcp_executor.py`

```python
"""McpToolExecutor: content flattening and McpError -> outcome conversion."""

from typing import Any

import pytest

from octave.agent.mcp_executor import McpToolExecutor
from octave.mcp.errors import (
    McpConfigError,
    McpConnectionError,
    McpNotConnectedError,
    McpRpcError,
    McpTimeoutError,
)
from octave.mcp.types import ToolContent, ToolResult


class StubRegistry:
    """Stands in for ToolRegistry; returns or raises what the test dictates."""

    def __init__(
        self, result: ToolResult | None = None, error: Exception | None = None
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def call_tool(
        self, id: str, name: str, arguments: dict[str, Any] | None = None
    ) -> ToolResult:
        self.calls.append((id, name, dict(arguments or {})))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


async def test_joins_text_blocks_and_passes_is_error_through() -> None:
    registry = StubRegistry(
        result=ToolResult(
            content=[ToolContent(kind="text", text="a"), ToolContent(kind="text", text="b")]
        )
    )
    outcome = await McpToolExecutor(registry).call("srv", "read", {"p": 1})  # type: ignore[arg-type]
    assert registry.calls == [("srv", "read", {"p": 1})]
    assert outcome.content == "a\nb"
    assert outcome.is_error is False


async def test_error_result_flags_outcome() -> None:
    registry = StubRegistry(
        result=ToolResult(
            content=[ToolContent(kind="text", text="disk full")], is_error=True
        )
    )
    outcome = await McpToolExecutor(registry).call("srv", "read", {})  # type: ignore[arg-type]
    assert outcome.content == "disk full"
    assert outcome.is_error is True


async def test_empty_content_becomes_sentinel() -> None:
    registry = StubRegistry(result=ToolResult(content=[]))
    outcome = await McpToolExecutor(registry).call("srv", "read", {})  # type: ignore[arg-type]
    assert outcome.content == "(no output)"


@pytest.mark.parametrize(
    "error",
    [
        McpRpcError("bad params", code=-32602),
        McpTimeoutError("timed out after 60s"),
        McpConnectionError("server died"),
        McpConfigError("unknown server id"),
        McpNotConnectedError("not connected"),
    ],
)
async def test_mcp_errors_become_error_outcomes(error: Exception) -> None:
    registry = StubRegistry(error=error)
    outcome = await McpToolExecutor(registry).call("srv", "read", {})  # type: ignore[arg-type]
    assert outcome.is_error is True
    assert str(error) in outcome.content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agent/test_mcp_executor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octave.agent.mcp_executor'`

- [ ] **Step 3: Implement** — create `backend/src/octave/agent/mcp_executor.py`

```python
"""ToolExecutor over ToolRegistry (issue #79).

The only octave.agent module importing octave.mcp. Converts McpError
subclasses into error ToolOutcomes (Tier-1, model-correctable) so the
loop never aborts on a tool failure.
"""

import logging
from typing import Any

from octave.agent.types import ToolOutcome
from octave.mcp import McpError, ToolRegistry

__all__ = ["McpToolExecutor"]

logger = logging.getLogger(__name__)

_NO_OUTPUT = "(no output)"


class McpToolExecutor:
    """Structural implementation of ``octave.agent.executor.ToolExecutor``."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def call(
        self, server_id: str, tool_name: str, arguments: dict[str, Any]
    ) -> ToolOutcome:
        """Invoke one tool; every failure mode becomes an error outcome."""
        try:
            result = await self._registry.call_tool(server_id, tool_name, arguments)
        except McpError as exc:
            logger.error(
                "MCP tool execution failed | server=%s tool=%s error=%s",
                server_id,
                tool_name,
                exc,
            )
            return ToolOutcome(content=f"Tool execution failed: {exc}", is_error=True)
        content = "\n".join(block.text for block in result.content)
        return ToolOutcome(content=content or _NO_OUTPUT, is_error=result.is_error)
```

Update `backend/src/octave/agent/__init__.py`: add `from octave.agent.mcp_executor import McpToolExecutor` and `"McpToolExecutor"` in `__all__` (alphabetical first).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agent -v`
Expected: all PASS — **including `test_package.py`**, which now goes green (full export surface + no SDK imports).

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/agent backend/tests/agent
git commit -m "feat(agent): add McpToolExecutor over ToolRegistry"
```

---

### Task 9: Full-suite gate, lint, types, push

**Files:** none new

- [ ] **Step 1: Full backend suite**

Run: `uv run pytest`
Expected: all PASS (0 failed). If a pre-existing test asserts old `Message` serialization or `role="tool"` rejection, it was missed — fix it to match the spec, do not skip.

- [ ] **Step 2: Lint**

Run: `uv run ruff check src tests`
Expected: `All checks passed!` (fix imports/ordering if flagged; `ruff format` not used by this repo)

- [ ] **Step 3: Types**

Run: `uv run mypy src`
Expected: `Success: no issues found in N source files`

- [ ] **Step 4: Push**

```bash
git push
```

Expected: branch `feature/tool-use-orchestration-loop` updated; draft PR #103 reflects the new commits.

- [ ] **Step 5: Confirm the PR is still draft and leave it for human review**

Run: `gh pr view 103 --json isDraft,title`
Expected: `isDraft: true` — do not mark ready or merge (release process owns that).

---

## Self-review notes (author)

- **Spec coverage:** §Data model → Tasks 2/6/7/8; adapter parse/serialize → Tasks 3/4; loop mechanics incl. limit semantics (N+1 completions, unfulfilled assistant message retained) → Task 7 tests; two-tier errors → Task 7 (Tier-1 in loop) + Task 8 (Tier-1 in executor) + Task 7 Step 5 (Tier-2 raise/propagate); package quarantine → Task 6 guard; deferrals untouched (streaming, parallel exec, wiring). Test 10's exact spec assertions mirrored in `test_loop_limit_raises_with_partial_transcript`.
- **Clarification applied:** OpenAI tool messages have no error flag; `Error: ` prefix conveys Tier-1 errors (spec intent: model sees failure as tool result). Spec §Testing item 14 phrasing "tool message is_error=True" maps to this prefix convention.
- **Type consistency:** `ToolOutcome(content, is_error)`, `ToolTurn(messages, result, tool_rounds)`, `ToolExecutor.call(server_id, tool_name, arguments)`, `ToolLoop(adapter=, executor=, max_tool_rounds=)`, `ToolLoop.run(messages, toolset, *, model=)`, `McpToolExecutor(registry)` — identical across all tasks and `__init__` exports.
