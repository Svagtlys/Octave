# Tool-Use Orchestration Loop Implementation Plan

**Goal:** Implement `run_tool_loop`, which detects `tool_calls` in an `InferenceAdapter.complete()` result, executes them concurrently via `McpClient`, appends results to the conversation, and triggers a follow-up completion — repeating until the LLM stops requesting tools or a safety limit is hit.

**Architecture:** Extend `octave.inference.types` with `ToolCall` and tool-call fields on `Message`/`CompletionResult`; teach `OpenAIAdapter` to translate them in both directions; add a new `octave.agent` package containing the loop itself, independent of any transport/endpoint.

**Tech Stack:** Python 3.12, pydantic v2, anyio (task groups), pytest (`asyncio_mode = "auto"`), httpx `MockTransport` for adapter tests, the existing in-memory MCP `harness()` fixture for loop tests.

**Design doc:** [`.agents/specs/2026-09-18-tool-use-orchestration-loop-design.md`](./2026-09-18-tool-use-orchestration-loop-design.md)

---

### Task 1: `ToolCall` type and tool-call fields on `Message`/`CompletionResult`

**Files:**
- Modify: `backend/src/octave/inference/types.py`
- Test: `backend/tests/inference/test_types.py`

- [x] **Step 1: Write the failing tests**

Append to `backend/tests/inference/test_types.py`:

```python
def test_tool_call_round_trips() -> None:
    call = ToolCall(id="call_1", name="echo", arguments={"text": "hi"})
    assert call.model_dump() == {
        "id": "call_1",
        "name": "echo",
        "arguments": {"text": "hi"},
    }


def test_message_defaults_have_no_tool_fields() -> None:
    message = Message(role="user", content="hi")
    assert message.tool_calls is None
    assert message.tool_call_id is None


def test_assistant_message_can_carry_tool_calls() -> None:
    call = ToolCall(id="call_1", name="echo", arguments={"text": "hi"})
    message = Message(role="assistant", content="", tool_calls=[call])
    assert message.tool_calls == [call]


def test_tool_message_carries_tool_call_id() -> None:
    message = Message(role="tool", content="hi back", tool_call_id="call_1")
    assert message.tool_call_id == "call_1"


def test_completion_result_defaults_tool_calls_to_none() -> None:
    result = CompletionResult(text="hi", model="m")
    assert result.tool_calls is None


def test_completion_result_can_carry_tool_calls() -> None:
    call = ToolCall(id="call_1", name="echo", arguments={})
    result = CompletionResult(text="", model="m", tool_calls=[call])
    assert result.tool_calls == [call]
```

Add `ToolCall` to that file's imports from `octave.inference.types`.

- [x] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/inference/test_types.py -v`
Expected: FAIL — `ImportError: cannot import name 'ToolCall'`

- [x] **Step 3: Implement**

In `backend/src/octave/inference/types.py`, replace the `Role`/`Message` block and
`CompletionResult` class:

```python
Role = Literal["system", "user", "assistant", "tool"]


class ToolCall(BaseModel):
    """One tool invocation requested by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


class Message(BaseModel):
    """A single chat message."""

    role: Role
    content: str
    tool_calls: list["ToolCall"] | None = None
    tool_call_id: str | None = None
```

And:

```python
class CompletionResult(BaseModel):
    """A full (non-streamed) chat completion."""

    text: str
    model: str
    finish_reason: str | None = None
    usage: Usage | None = None
    tool_calls: list["ToolCall"] | None = None
```

Add `"ToolCall"` to the module's `__all__` list (alphabetical, after `"Message"`... actually
alphabetically it sorts before `"Usage"` and after `"ModelInfo"` — insert as:
`"CompletionChunk", "CompletionRequest", "CompletionResult", "EmbeddingRequest", "EmbeddingResult", "Message", "ModelInfo", "ToolCall", "Usage"`).

- [x] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/inference/test_types.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add backend/src/octave/inference/types.py backend/tests/inference/test_types.py
git commit -m "feat(inference): add ToolCall type and tool-call fields to Message/CompletionResult"
```

---

### Task 2: `OpenAIAdapter` reads/writes `tool_calls`

**Files:**
- Modify: `backend/src/octave/inference/openai_adapter.py`
- Test: `backend/tests/inference/test_openai_adapter.py`

- [x] **Step 1: Write the failing tests**

Add a new canned response and test cases to `backend/tests/inference/test_openai_adapter.py`.
Add near the top, after `CHAT_RESPONSE`:

```python
TOOL_CALL_RESPONSE = {
    "id": "chatcmpl-456",
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
                            "name": "echo",
                            "arguments": '{"text": "hi"}',
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 9, "completion_tokens": 12, "total_tokens": 21},
}
```

Extend `_handler` so a request whose last message content is `"trigger tool call"` returns
`TOOL_CALL_RESPONSE`. In `_handler`, add this branch before the final `return httpx.Response(200, json=CHAT_RESPONSE)`:

```python
        if content == "trigger tool call":
            return httpx.Response(200, json=TOOL_CALL_RESPONSE)
```

Then add these test functions at the end of the file:

```python
async def test_tool_calls_translate_from_sdk_response() -> None:
    client = _mock_client()
    adapter = _adapter(client)
    result = await adapter.complete(
        CompletionRequest(
            model=None,
            messages=[Message(role="user", content="trigger tool call")],
        )
    )
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls == [
        ToolCall(id="call_1", name="echo", arguments={"text": "hi"})
    ]
    await adapter.aclose()
    await client.aclose()


async def test_response_without_tool_calls_leaves_field_none() -> None:
    client = _mock_client()
    adapter = _adapter(client)
    result = await adapter.complete(
        CompletionRequest(model=None, messages=[Message(role="user", content="hi")])
    )
    assert result.tool_calls is None
    await adapter.aclose()
    await client.aclose()


async def test_outgoing_tool_calls_serialize_arguments_as_json_string() -> None:
    captured: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=CHAT_RESPONSE)

    client = _mock_client(capture)
    adapter = _adapter(client)
    call = ToolCall(id="call_1", name="echo", arguments={"text": "hi"})
    await adapter.complete(
        CompletionRequest(
            model=None,
            messages=[
                Message(role="user", content="hi"),
                Message(role="assistant", content="", tool_calls=[call]),
                Message(role="tool", content="hi back", tool_call_id="call_1"),
            ],
        )
    )
    sent_messages = json.loads(captured[0].content)["messages"]
    assert sent_messages[1]["tool_calls"] == [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "echo", "arguments": '{"text": "hi"}'},
        }
    ]
    assert sent_messages[2]["tool_call_id"] == "call_1"
    assert "tool_calls" not in sent_messages[0]
    assert "tool_call_id" not in sent_messages[0]
    await adapter.aclose()
    await client.aclose()
```

Add `ToolCall` to the `octave.inference.types` import line in this test file.

- [x] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/inference/test_openai_adapter.py -v -k tool_call`
Expected: FAIL — `tool_calls` not read from response (assertion `result.tool_calls is None` fails
first test), and the outgoing serialization test fails with a `KeyError`/mismatched shape.

- [x] **Step 3: Implement**

In `backend/src/octave/inference/openai_adapter.py`:

1. Add `import json` to the top-level imports.
2. Add `ToolCall` to the `from octave.inference.types import (...)` block.
3. Add a helper near `_chat_usage`:

```python
def _chat_tool_calls(
    tool_calls: list[Any] | None,
) -> list[ToolCall] | None:
    if not tool_calls:
        return None
    return [
        ToolCall(
            id=call.id,
            name=call.function.name,
            arguments=json.loads(call.function.arguments),
        )
        for call in tool_calls
    ]
```

4. In `complete()`, pass the translated tool calls through:

```python
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
            tool_calls=_chat_tool_calls(choice.message.tool_calls),
        )
```

5. In `_chat_kwargs()`, replace the messages line and add serialization of outgoing
   `tool_calls`:

```python
    def _chat_kwargs(self, request: CompletionRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self._resolve_model(request.model),
            "messages": [self._dump_message(message) for message in request.messages],
        }
```

(leave the rest of the method body unchanged) and add a new method:

```python
    def _dump_message(self, message: Message) -> dict[str, Any]:
        dumped = message.model_dump(exclude_none=True)
        tool_calls = dumped.get("tool_calls")
        if tool_calls:
            dumped["tool_calls"] = [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call["arguments"]),
                    },
                }
                for call in tool_calls
            ]
        return dumped
```

6. Add `Message` to the `from octave.inference.types import (...)` block (needed for the
   `_dump_message` type hint).

- [x] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/inference/test_openai_adapter.py -v`
Expected: PASS (all tests in the file, including the pre-existing conformance suite)

- [x] **Step 5: Commit**

```bash
git add backend/src/octave/inference/openai_adapter.py backend/tests/inference/test_openai_adapter.py
git commit -m "feat(inference): translate tool_calls in OpenAIAdapter request/response"
```

---

### Task 3: Scripted fake adapter for loop tests

**Files:**
- Modify: `backend/tests/inference/fakes.py`

- [x] **Step 1: Write the failing test**

Create `backend/tests/agent/__init__.py` (empty file, matches `tests/inference`/`tests/mcp`
convention) and `backend/tests/agent/test_scripted_adapter.py`:

```python
"""Sanity check for the scripted test double used by tool-loop tests."""

import pytest

from octave.inference.config import AdapterConfig
from octave.inference.types import CompletionRequest, CompletionResult, Message
from tests.inference.fakes import ScriptedAdapter


async def test_scripted_adapter_returns_queued_results_in_order() -> None:
    first = CompletionResult(text="", model="m", finish_reason="tool_calls")
    second = CompletionResult(text="done", model="m", finish_reason="stop")
    adapter = ScriptedAdapter(AdapterConfig(adapter="scripted"), responses=[first, second])
    request = CompletionRequest(messages=[Message(role="user", content="hi")])

    assert await adapter.complete(request) is first
    assert await adapter.complete(request) is second


async def test_scripted_adapter_raises_when_exhausted() -> None:
    adapter = ScriptedAdapter(AdapterConfig(adapter="scripted"), responses=[])
    request = CompletionRequest(messages=[Message(role="user", content="hi")])
    with pytest.raises(IndexError):
        await adapter.complete(request)
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/agent/test_scripted_adapter.py -v`
Expected: FAIL — `ImportError: cannot import name 'ScriptedAdapter'`

- [x] **Step 3: Implement**

Append to `backend/tests/inference/fakes.py` (add `import copy` and `CompletionResult` is
already imported):

```python
class ScriptedAdapter(FakeAdapter):
    """Returns a pre-scripted sequence of results, one per ``complete()`` call.

    Used to drive the tool-use orchestration loop deterministically: script a
    ``tool_calls`` result followed by a ``stop`` result.
    """

    def __init__(
        self, config: AdapterConfig, *, responses: list[CompletionResult]
    ) -> None:
        super().__init__(config)
        self._responses = list(responses)

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        self.complete_calls.append(request)
        return self._responses.pop(0)
```

Add `"ScriptedAdapter"` to the file's `__all__` list.

- [x] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_scripted_adapter.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add backend/tests/inference/fakes.py backend/tests/agent/__init__.py backend/tests/agent/test_scripted_adapter.py
git commit -m "test(inference): add ScriptedAdapter double for tool-loop tests"
```

---

### Task 4: `octave.agent` package skeleton — errors

**Files:**
- Create: `backend/src/octave/agent/__init__.py`
- Create: `backend/src/octave/agent/errors.py`
- Test: `backend/tests/agent/test_errors.py`

- [x] **Step 1: Write the failing test**

```python
"""octave.agent exception hierarchy."""

import pytest

from octave.agent.errors import AgentError, ToolLoopMaxIterationsError


def test_tool_loop_max_iterations_is_an_agent_error() -> None:
    error = ToolLoopMaxIterationsError(10)
    assert isinstance(error, AgentError)
    assert error.max_iterations == 10
    assert "10" in str(error)
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/agent/test_errors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octave.agent'`

- [x] **Step 3: Implement**

`backend/src/octave/agent/errors.py`:

```python
"""Octave agent orchestration exception hierarchy.

Same fail-loud philosophy as ``octave.inference.errors`` and
``octave.mcp.errors``: a stuck or misbehaving loop raises a named Octave
type rather than looping forever or failing silently.
"""

__all__ = ["AgentError", "ToolLoopMaxIterationsError"]


class AgentError(Exception):
    """Base class for every agent orchestration failure."""


class ToolLoopMaxIterationsError(AgentError):
    """The LLM kept requesting tools past the configured iteration limit."""

    def __init__(self, max_iterations: int) -> None:
        self.max_iterations = max_iterations
        super().__init__(f"Tool loop exceeded {max_iterations} iterations")
```

`backend/src/octave/agent/__init__.py`:

```python
"""Agent orchestration: the reason -> act -> observe tool-use loop.

Imports both ``octave.inference`` and ``octave.mcp`` through their public
seams; neither of those packages imports this one.
"""

from octave.agent.errors import AgentError, ToolLoopMaxIterationsError

__all__ = ["AgentError", "ToolLoopMaxIterationsError"]
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_errors.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add backend/src/octave/agent/__init__.py backend/src/octave/agent/errors.py backend/tests/agent/test_errors.py
git commit -m "feat(agent): scaffold octave.agent package with error hierarchy"
```

---

### Task 5: `run_tool_loop` — single tool call, happy path

**Files:**
- Create: `backend/src/octave/agent/tool_loop.py`
- Test: `backend/tests/agent/test_tool_loop.py`

- [x] **Step 1: Write the failing test**

`backend/tests/agent/test_tool_loop.py`:

```python
"""run_tool_loop: reason -> act -> observe over a real in-memory MCP session."""

from octave.agent.tool_loop import run_tool_loop
from octave.inference.config import AdapterConfig
from octave.inference.types import (
    CompletionRequest,
    CompletionResult,
    Message,
    ToolCall,
)
from tests.inference.fakes import ScriptedAdapter
from tests.mcp.conftest import harness_cm


def _scripted(*responses: CompletionResult) -> ScriptedAdapter:
    return ScriptedAdapter(AdapterConfig(adapter="scripted"), responses=list(responses))


async def test_single_tool_call_triggers_follow_up_completion() -> None:
    tool_call = ToolCall(id="call_1", name="echo", arguments={"text": "hi"})
    first = CompletionResult(
        text="", model="m", finish_reason="tool_calls", tool_calls=[tool_call]
    )
    second = CompletionResult(text="done", model="m", finish_reason="stop")
    adapter = _scripted(first, second)
    request = CompletionRequest(messages=[Message(role="user", content="say hi")])

    async with harness_cm() as (mcp_client, _peer):
        result = await run_tool_loop(adapter, mcp_client, request)

    assert result is second
    assert len(adapter.complete_calls) == 2
    history = adapter.complete_calls[1].messages
    assert history[0] == Message(role="user", content="say hi")
    assert history[1] == Message(role="assistant", content="", tool_calls=[tool_call])
    assert history[2].role == "tool"
    assert history[2].tool_call_id == "call_1"
    assert history[2].content == "hi"
```

- [x] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/agent/test_tool_loop.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octave.agent.tool_loop'`

- [x] **Step 3: Implement**

`backend/src/octave/agent/tool_loop.py`:

```python
"""The reason -> act -> observe tool-use orchestration loop."""

import anyio

from octave.agent.errors import ToolLoopMaxIterationsError
from octave.inference.adapter import InferenceAdapter
from octave.inference.types import CompletionRequest, CompletionResult, Message, ToolCall
from octave.mcp.client import McpClient
from octave.mcp.errors import McpError

__all__ = ["run_tool_loop"]


async def run_tool_loop(
    adapter: InferenceAdapter,
    mcp_client: McpClient,
    request: CompletionRequest,
    *,
    max_iterations: int = 10,
) -> CompletionResult:
    """Run reason -> act -> observe until the LLM stops requesting tools.

    Each iteration calls ``adapter.complete()``. When the result carries
    ``finish_reason == "tool_calls"``, every requested tool is executed
    concurrently via ``mcp_client.call_tool`` and the results are appended
    to the conversation before the next completion. Returns the first
    result whose ``finish_reason`` is not ``"tool_calls"``.

    Raises ``ToolLoopMaxIterationsError`` if the LLM keeps requesting tools
    past ``max_iterations``.
    """
    messages = list(request.messages)
    for _ in range(max_iterations):
        result = await adapter.complete(request.model_copy(update={"messages": messages}))
        if result.finish_reason != "tool_calls" or not result.tool_calls:
            return result
        messages.append(
            Message(role="assistant", content=result.text, tool_calls=result.tool_calls)
        )
        messages.extend(await _execute_tool_calls(mcp_client, result.tool_calls))
    raise ToolLoopMaxIterationsError(max_iterations)


async def _execute_tool_calls(
    mcp_client: McpClient, tool_calls: list[ToolCall]
) -> list[Message]:
    results: list[Message | None] = [None] * len(tool_calls)

    async def run_one(index: int, call: ToolCall) -> None:
        results[index] = await _call_and_wrap(mcp_client, call)

    async with anyio.create_task_group() as tg:
        for index, call in enumerate(tool_calls):
            tg.start_soon(run_one, index, call)
    return [message for message in results if message is not None]


async def _call_and_wrap(mcp_client: McpClient, call: ToolCall) -> Message:
    try:
        tool_result = await mcp_client.call_tool(call.name, call.arguments)
    except McpError as exc:
        return Message(role="tool", content=f"Error: {exc}", tool_call_id=call.id)
    content = "".join(block.text for block in tool_result.content)
    return Message(role="tool", content=content, tool_call_id=call.id)
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_tool_loop.py -v`
Expected: PASS

- [x] **Step 5: Commit**

```bash
git add backend/src/octave/agent/tool_loop.py backend/tests/agent/test_tool_loop.py
git commit -m "feat(agent): implement run_tool_loop for single tool-call turns"
```

---

### Task 6: Multi-tool-call ordering, error surfacing, and max-iterations

**Files:**
- Modify: `backend/tests/agent/test_tool_loop.py`

- [x] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_tool_loop.py`:

```python
async def test_multiple_tool_calls_preserve_request_order() -> None:
    calls = [
        ToolCall(id="call_1", name="slow", arguments={"text": "first"}),
        ToolCall(id="call_2", name="echo", arguments={"text": "second"}),
    ]
    first = CompletionResult(
        text="", model="m", finish_reason="tool_calls", tool_calls=calls
    )
    second = CompletionResult(text="done", model="m", finish_reason="stop")
    adapter = _scripted(first, second)
    request = CompletionRequest(messages=[Message(role="user", content="go")])

    async with harness_cm(request_timeout=0.2) as (mcp_client, _peer):
        # "slow" sleeps 30s and would time out; use a short-timeout client
        # so this test doesn't hang, and assert its failure surfaces as an
        # error tool message rather than crashing the loop.
        result = await run_tool_loop(adapter, mcp_client, request)

    assert result is second
    history = adapter.complete_calls[1].messages
    tool_messages = [m for m in history if m.role == "tool"]
    assert [m.tool_call_id for m in tool_messages] == ["call_1", "call_2"]
    assert tool_messages[0].content.startswith("Error:")
    assert tool_messages[1].content == "second"


async def test_mcp_level_tool_error_surfaces_as_tool_message() -> None:
    tool_call = ToolCall(id="call_1", name="tool_error", arguments={})
    first = CompletionResult(
        text="", model="m", finish_reason="tool_calls", tool_calls=[tool_call]
    )
    second = CompletionResult(text="done", model="m", finish_reason="stop")
    adapter = _scripted(first, second)
    request = CompletionRequest(messages=[Message(role="user", content="go")])

    async with harness_cm() as (mcp_client, _peer):
        result = await run_tool_loop(adapter, mcp_client, request)

    assert result is second
    tool_message = adapter.complete_calls[1].messages[-1]
    assert tool_message.content == "tool failed"


async def test_unknown_tool_surfaces_rpc_error_as_tool_message() -> None:
    tool_call = ToolCall(id="call_1", name="does_not_exist", arguments={})
    first = CompletionResult(
        text="", model="m", finish_reason="tool_calls", tool_calls=[tool_call]
    )
    second = CompletionResult(text="done", model="m", finish_reason="stop")
    adapter = _scripted(first, second)
    request = CompletionRequest(messages=[Message(role="user", content="go")])

    async with harness_cm() as (mcp_client, _peer):
        result = await run_tool_loop(adapter, mcp_client, request)

    assert result is second
    tool_message = adapter.complete_calls[1].messages[-1]
    assert tool_message.content.startswith("Error:")


async def test_exceeding_max_iterations_raises() -> None:
    tool_call = ToolCall(id="call_1", name="echo", arguments={"text": "hi"})
    always_tool_calls = CompletionResult(
        text="", model="m", finish_reason="tool_calls", tool_calls=[tool_call]
    )
    adapter = _scripted(*([always_tool_calls] * 3))
    request = CompletionRequest(messages=[Message(role="user", content="go")])

    async with harness_cm() as (mcp_client, _peer):
        with pytest.raises(ToolLoopMaxIterationsError) as excinfo:
            await run_tool_loop(adapter, mcp_client, request, max_iterations=3)

    assert excinfo.value.max_iterations == 3
```

Add `import pytest` and `from octave.agent.errors import ToolLoopMaxIterationsError` to the
top of `test_tool_loop.py`.

- [x] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/agent/test_tool_loop.py -v`
Expected: The four new tests FAIL or hang — in particular the "slow" tool test will hit the
real 30s sleep unless the harness timeout is already wired (it is, via `request_timeout=0.2`
passed to `harness_cm`, which the existing MCP client design already honors — see
`McpClient._run`). If any test hangs past ~1s, re-check that `request_timeout` is being
passed through.

- [x] **Step 3: Implement**

No production code changes are needed for this task — Task 5's `run_tool_loop` and
`_call_and_wrap` already handle concurrent execution, order preservation (indexed list),
`is_error=True` results, and `McpError` translation. This task exists to *prove* those
properties with tests the design specifically calls for. If any test fails, the likely gap is:

- Order not preserved → check `_execute_tool_calls` writes into `results[index]`, not
  `results.append(...)`.
- The "slow" tool test hangs → confirm `harness_cm(request_timeout=0.2)` is passed and that
  `_call_and_wrap` catches `McpError` (which includes `McpTimeoutError`).

- [x] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_tool_loop.py -v`
Expected: PASS, in well under a second (no real 30s wait — the client's own request timeout
fires first).

- [x] **Step 5: Commit**

```bash
git add backend/tests/agent/test_tool_loop.py
git commit -m "test(agent): cover multi-tool ordering, error surfacing, and max-iterations"
```

---

### Task 7: Full suite, lint, type-check, and docs

**Files:**
- Modify: `docs/ARCHITECTURE.md`

- [x] **Step 1: Run the full backend test suite**

Run: `cd backend && uv run pytest -v`
Expected: PASS, no regressions in `tests/inference/`, `tests/mcp/`, `tests/agent/`, or
elsewhere.

- [x] **Step 2: Lint and type-check**

Run: `cd backend && uv run ruff check . && uv run mypy src`
Expected: No errors. Fix any `ruff`/`mypy` findings in the new/modified files before
proceeding (e.g. missing return type on a helper, unused import).

- [x] **Step 3: Update architecture docs**

In `docs/ARCHITECTURE.md`, under the **Inference Engine Connector** section, replace the
line `Requests tool invocations through the **MCP Connector** when the LLM outputs tool
calls` bullet's implication of "not yet built" by adding a new paragraph after the existing
"Implemented — adapter contract (issue #8)" paragraph:

```markdown
**Implemented — tool-use orchestration loop (issue #79):** `octave.agent.run_tool_loop`
drives the reason → act → observe cycle: it calls `InferenceAdapter.complete()`, and when
the result's `finish_reason` is `"tool_calls"`, executes every requested tool concurrently
via `McpClient.call_tool`, appends the results to the conversation, and triggers a
follow-up completion — up to a configurable `max_iterations` safety limit. Tool execution
failures (MCP-level or transport-level) surface back to the LLM as ordinary tool-result
messages rather than aborting the turn. Sending tool *definitions* to the LLM in the first
place is a separate concern (issue #78).
```

- [x] **Step 4: Commit**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: note tool-use orchestration loop in architecture doc"
```

- [x] **Step 5: Push and mark the PR ready for review**

```bash
git push origin feature/tool-use-orchestration-loop
```

Then mark PR [#92](https://github.com/Svagtlys/Octave/pull/92) ready for review (undraft) and
check off the PR's checklist items (implementation complete, tests passing).

---

## Self-Review Notes

- **Spec coverage:** every scope bullet in the issue is covered — `tool_calls` field
  (Task 1), `finish_reason == "tool_calls"` routing (Task 5), name+arguments → MCP
  `tools/call` mapping (Task 5's `_call_and_wrap`), provider-native tool message format
  (Task 2), append + follow-up completion (Task 5), multi-tool-call support (Task 6),
  and error handling (Task 5 + Task 6).
- **Type consistency:** `ToolCall(id, name, arguments)` is defined once in Task 1 and used
  identically in every later task; `run_tool_loop(adapter, mcp_client, request, *,
  max_iterations=10)` signature is fixed in Task 5 and unchanged after.
- **No placeholders:** every step above contains complete, runnable code.
