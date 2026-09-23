# Tool Schema Translation Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Translate MCP tool inventories (`ToolInfo.input_schema`) into OpenAI-compatible function tool definitions carried on `CompletionRequest.tools`, with `mcp__<server>__<tool>` dedupe and a reverse-routing map.

**Architecture:** A new pure `octave/tools/` package converts `octave.mcp` inventory objects into `octave.inference` `ToolDefinition`s — neither subsystem imports the other. `OpenAIAdapter._chat_kwargs` wraps definitions into the `{"type": "function", ...}` wire envelope. No runtime wiring: the agent-loop consumer is a later work item.

**Tech Stack:** Python 3.12, pydantic v2, pytest (asyncio auto-mode), `httpx.MockTransport` wire tests, uv, ruff, mypy strict.

**Design spec:** [`.agents/specs/2026-09-22-tool-schema-translation-design.md`](2026-09-22-tool-schema-translation-design.md)
**Issue:** [#78](https://github.com/Svagtlys/Octave/issues/78) · **Draft PR:** [#100](https://github.com/Svagtlys/Octave/pull/100) · **Branch:** `feature/tool-schema-translation-layer`

---

## Conventions for this plan

- All commands run from the repo root (`Octave/`). Python commands are `cd backend && uv run …`.
- Work on branch `feature/tool-schema-translation-layer` (already checked out by `start-work-item`).
- Test files import from `octave.*` (pytest `pythonpath = ["src"]` is configured in [`backend/pyproject.toml`](../backend/pyproject.toml)).
- mypy is strict (`backend/pyproject.toml`); annotate everything. Ruff line-length 88.
- Commit format: `type(scope): description` (per [`.agents/rules/coding.md`](../.agents/rules/coding.md)).

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `backend/src/octave/inference/types.py` | Modify | Add `ToolDefinition` model; `CompletionRequest.tools` field |
| `backend/src/octave/inference/__init__.py` | Modify | Re-export `ToolDefinition` |
| `backend/src/octave/inference/openai_adapter.py` | Modify | `_chat_kwargs` emits the `tools` envelope |
| `backend/src/octave/tools/__init__.py` | Create | Public façade of the tool plane |
| `backend/src/octave/tools/errors.py` | Create | `ToolTranslationError`, `ToolNameCollisionError` |
| `backend/src/octave/tools/types.py` | Create | `ToolRoute`, `ProviderToolset` (translation output vocabulary) |
| `backend/src/octave/tools/translate.py` | Create | `translate_tools()` — pure MCP → provider conversion |
| `backend/tests/tools/__init__.py` | Create | Package marker |
| `backend/tests/tools/test_package.py` | Create | Public-surface + no-SDK-import guard |
| `backend/tests/tools/test_translate.py` | Create | Translator unit tests |
| `backend/tests/inference/test_types.py` | Modify | `tools` field defaults/round-trip |
| `backend/tests/inference/test_package.py` | Modify | `ToolDefinition` in public names |
| `backend/tests/inference/test_openai_adapter.py` | Modify | Wire envelope tests |
| `docs/ARCHITECTURE.md` | Modify | Record the tool plane |

---

### Task 1: Commit the approved design doc

**Files:**
- Commit: `.agents/specs/2026-09-22-tool-schema-translation-design.md` (already written)

- [ ] **Step 1: Verify branch and file presence**

```bash
git branch --show-current
git status --short .agents/specs/
```
Expected: `feature/tool-schema-translation-layer`; `?? ../.agents/specs/2026-09-22-tool-schema-translation-design.md` (or `A`).

- [ ] **Step 2: Commit**

```bash
git add .agents/specs/2026-09-22-tool-schema-translation-design.md
git commit -m "docs(specs): add tool schema translation design (#78)"
```

---

### Task 2: `ToolDefinition` + `CompletionRequest.tools`

**Files:**
- Modify: `backend/src/octave/inference/types.py`
- Modify: `backend/src/octave/inference/__init__.py`
- Modify: `backend/tests/inference/test_types.py`
- Modify: `backend/tests/inference/test_package.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/inference/test_types.py`:

```python
def test_completion_request_tools_defaults_none() -> None:
    request = CompletionRequest(model=None, messages=[])
    assert request.tools is None


def test_completion_request_round_trips_tools() -> None:
    tool = ToolDefinition(
        name="mcp__fs__read",
        description="Read a file",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    request = CompletionRequest(model="m", messages=[], tools=[tool])
    assert request.tools == [tool]


def test_tool_definition_defaults() -> None:
    tool = ToolDefinition(name="x")
    assert tool.description is None
    assert tool.parameters == {}
    assert ToolDefinition(name="y").parameters is not tool.parameters
```

Add `ToolDefinition` to the existing import block at the top of the file:

```python
from octave.inference.types import (
    CompletionChunk,
    CompletionRequest,
    EmbeddingRequest,
    Message,
    ModelInfo,
    ToolDefinition,
)
```

Append to the tuple in `backend/tests/inference/test_package.py::test_public_names_are_exported` (inside the existing `for name in (...)`):

```python
        "ToolDefinition",
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/inference/test_types.py tests/inference/test_package.py -v
```
Expected: FAIL / collection error — `ImportError: cannot import name 'ToolDefinition'`.

- [ ] **Step 3: Implement** — in `backend/src/octave/inference/types.py`, add `"ToolDefinition",` to `__all__` (alphabetical position after `"Usage"` is not required; keep the list sorted as-is by inserting after `"ModelInfo",`), and add the model after `Message` (before `Usage`):

```python
class ToolDefinition(BaseModel):
    """One tool in Octave's provider-neutral shape.

    ``parameters`` is a JSON Schema object; the adapter wraps this model
    into the provider envelope. SDK types never appear here.
    """

    name: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
```

Add the field to `CompletionRequest` (after `stop`, before `extra`):

```python
    tools: list[ToolDefinition] | None = None
```

In `backend/src/octave/inference/__init__.py`, add `ToolDefinition` to the `from octave.inference.types import (...)` block (after `ModelInfo`) and `"ToolDefinition",` to `__all__` (after `"OpenAIAdapter",`).

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/inference/test_types.py tests/inference/test_package.py -v
```
Expected: PASS (all, including pre-existing tests).

- [ ] **Step 5: Lint/type gate + commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/octave/inference/types.py backend/src/octave/inference/__init__.py backend/tests/inference/test_types.py backend/tests/inference/test_package.py
git commit -m "feat(inference): add ToolDefinition and tools field to CompletionRequest"
```
Expected: ruff and mypy exit 0.

---

### Task 3: Adapter forwards the `tools` envelope

**Files:**
- Modify: `backend/src/octave/inference/openai_adapter.py` (`_chat_kwargs`, ~line 158)
- Modify: `backend/tests/inference/test_openai_adapter.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/inference/test_openai_adapter.py`:

```python
def _tools_request() -> CompletionRequest:
    return CompletionRequest(
        model=None,
        messages=[Message(role="user", content="hi")],
        tools=[
            ToolDefinition(
                name="mcp__fs__read",
                description="Read a file",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            )
        ],
    )


async def test_tools_wrap_in_function_envelope() -> None:
    captured: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=CHAT_RESPONSE)

    client = _mock_client(capture)
    adapter = _adapter(client)
    await adapter.complete(_tools_request())
    assert json.loads(captured[0].content)["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "mcp__fs__read",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
        }
    ]
    await adapter.aclose()
    await client.aclose()


async def test_stream_sends_tools_envelope() -> None:
    captured: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            text=_sse(STREAM_CHUNKS),
            headers={"content-type": "text/event-stream"},
        )

    client = _mock_client(capture)
    adapter = _adapter(client)
    async for _ in adapter.stream(_tools_request()):
        pass
    assert json.loads(captured[0].content)["tools"][0]["type"] == "function"
    await adapter.aclose()
    await client.aclose()


async def test_tools_key_absent_when_none_or_empty() -> None:
    captured: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=CHAT_RESPONSE)

    client = _mock_client(capture)
    adapter = _adapter(client)
    for tools in (None, []):
        await adapter.complete(
            CompletionRequest(
                model=None, messages=[Message(role="user", content="hi")], tools=tools
            )
        )
    assert all("tools" not in json.loads(r.content) for r in captured)
    await adapter.aclose()
    await client.aclose()
```

Extend the existing import: `from octave.inference.types import CompletionRequest, Message, ToolDefinition`.

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/inference/test_openai_adapter.py -k "tools" -v
```
Expected: the two envelope tests FAIL (`KeyError: 'tools'`); the absent test passes already (it asserts absence).

- [ ] **Step 3: Implement** — in `backend/src/octave/inference/openai_adapter.py::_chat_kwargs`, insert after the `stop` block (before the `extra` block):

```python
        if request.tools:
            kwargs["tools"] = [
                {"type": "function", "function": tool.model_dump(exclude_none=True)}
                for tool in request.tools
            ]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/inference/test_openai_adapter.py -v
```
Expected: PASS (new + all pre-existing adapter tests, including the conformance class).

- [ ] **Step 5: Lint/type gate + commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/octave/inference/openai_adapter.py backend/tests/inference/test_openai_adapter.py
git commit -m "feat(inference): forward tools envelope in OpenAI adapter chat kwargs"
```

---

### Task 4: `octave.tools` package scaffold (errors + output types + hygiene test)

**Files:**
- Create: `backend/src/octave/tools/__init__.py`
- Create: `backend/src/octave/tools/errors.py`
- Create: `backend/src/octave/tools/types.py`
- Create: `backend/tests/tools/__init__.py` (empty)
- Create: `backend/tests/tools/test_package.py`

- [ ] **Step 1: Write the failing test** — create `backend/tests/tools/test_package.py`:

```python
"""Public API surface and SDK-quarantine posture of the tool plane."""

import ast
from pathlib import Path

import octave.tools as tools_pkg

SDK_MODULES = {"openai", "mcp"}


def _imported_top_level_modules() -> set[str]:
    names: set[str] = set()
    for path in Path(tools_pkg.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_no_sdk_imports() -> None:
    """octave.tools touches Octave façades only, never the openai/mcp SDKs."""
    assert not _imported_top_level_modules() & SDK_MODULES


def test_public_names_are_exported() -> None:
    for name in (
        "ProviderToolset",
        "ToolRoute",
        "ToolTranslationError",
        "ToolNameCollisionError",
    ):
        assert hasattr(tools_pkg, name), name
```

Create empty `backend/tests/tools/__init__.py`.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd backend && uv run pytest tests/tools -v
```
Expected: collection error — `ModuleNotFoundError: No module named 'octave.tools'`.

- [ ] **Step 3: Implement** — create `backend/src/octave/tools/errors.py`:

```python
"""Translation-layer errors (design spec #78)."""

__all__ = ["ToolNameCollisionError", "ToolTranslationError"]


class ToolTranslationError(Exception):
    """Base for tool translation failures."""


class ToolNameCollisionError(ToolTranslationError):
    """Two tools landed on the same exposed name after sanitization."""
```

Create `backend/src/octave/tools/types.py`:

```python
"""Translator output vocabulary (design spec #78)."""

from pydantic import BaseModel

from octave.inference.types import ToolDefinition

__all__ = ["ProviderToolset", "ToolRoute"]


class ToolRoute(BaseModel):
    """Reverse-resolution target for one exposed tool name."""

    server_id: str
    """Registry addressing key: ``ToolRegistry.call_tool(server_id, ...)``."""

    tool_name: str
    """Original (unprefixed, unsanitized) MCP tool name."""


class ProviderToolset(BaseModel):
    """Translator output: what the engine sees + how to route calls back."""

    tools: list[ToolDefinition]
    routes: dict[str, ToolRoute]
    """Exposed name -> route. Keys match ``tools[*].name`` exactly."""
```

Create `backend/src/octave/tools/__init__.py`:

```python
"""Tool plane: MCP -> provider schema translation (issue #78).

Pure functions over the Octave vocabularies of octave.mcp and
octave.inference. Neither subsystem imports the other; no SDK crosses
this boundary (guarded by tests/tools/test_package.py).
"""

from octave.tools.errors import ToolNameCollisionError, ToolTranslationError
from octave.tools.types import ProviderToolset, ToolRoute

__all__ = [
    "ProviderToolset",
    "ToolNameCollisionError",
    "ToolRoute",
    "ToolTranslationError",
]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/tools -v
```
Expected: 2 passed.

- [ ] **Step 5: Lint/type gate + commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/octave/tools backend/tests/tools
git commit -m "feat(tools): scaffold octave.tools package with output types and errors"
```

---

### Task 5: `translate_tools` — exposed names, routes, collisions, ordering

**Files:**
- Create: `backend/src/octave/tools/translate.py`
- Modify: `backend/src/octave/tools/__init__.py`
- Modify: `backend/tests/tools/test_package.py`
- Create: `backend/tests/tools/test_translate.py`

- [ ] **Step 1: Write the failing tests** — create `backend/tests/tools/test_translate.py`:

```python
"""translate_tools: naming, dedupe, routes, ordering (design spec #78)."""

import pytest
from octave.mcp import ServerToolInventory, ToolInfo
from octave.tools import (
    ProviderToolset,
    ToolNameCollisionError,
    ToolRoute,
    translate_tools,
)


def _tool(
    name: str = "read_file",
    description: str | None = None,
    schema: dict | None = None,
) -> ToolInfo:
    return ToolInfo(
        name=name,
        description=description,
        input_schema=schema or {"type": "object", "properties": {}},
    )


def _inventory(
    server_id: str = "srv-1",
    server_name: str = "fs",
    tools: list[ToolInfo] | None = None,
) -> ServerToolInventory:
    return ServerToolInventory(
        server_id=server_id,
        server_name=server_name,
        state="connected",
        tools=tools if tools is not None else [_tool()],
    )


def test_translates_single_tool() -> None:
    result = translate_tools([_inventory()])
    assert isinstance(result, ProviderToolset)
    assert [t.name for t in result.tools] == ["mcp__fs__read_file"]
    assert result.tools[0].parameters == {"type": "object", "properties": {}}
    assert result.routes == {
        "mcp__fs__read_file": ToolRoute(server_id="srv-1", tool_name="read_file")
    }


def test_multi_server_ordering_and_routes() -> None:
    result = translate_tools(
        [
            _inventory("srv-a", "alpha", [_tool("one"), _tool("two")]),
            _inventory("srv-b", "beta", [_tool("three")]),
        ]
    )
    assert [t.name for t in result.tools] == [
        "mcp__alpha__one",
        "mcp__alpha__two",
        "mcp__beta__three",
    ]
    assert result.routes["mcp__beta__three"].server_id == "srv-b"


def test_sanitizes_invalid_characters() -> None:
    result = translate_tools(
        [_inventory(server_name="my.fs:srv", tools=[_tool("get.file!")])]
    )
    assert result.tools[0].name == "mcp__my_fs_srv__get_file_"


def test_truncates_to_64_chars() -> None:
    result = translate_tools(
        [_inventory(server_name="s" * 40, tools=[_tool("t" * 40)])]
    )
    name = result.tools[0].name
    assert len(name) == 64
    assert name == f"mcp__{'s' * 40}__{'t' * 40}"[:64]
    assert list(result.routes) == [name]


def test_collision_raises_naming_both_sources() -> None:
    with pytest.raises(ToolNameCollisionError) as excinfo:
        translate_tools(
            [
                _inventory("srv-a", "fs!", [_tool("read")]),
                _inventory("srv-b", "fs:", [_tool("read")]),
            ]
        )
    message = str(excinfo.value)
    assert "srv-a/read" in message
    assert "srv-b/read" in message


def test_routes_keep_original_unsanitized_tool_name() -> None:
    result = translate_tools(
        [_inventory("srv-9", "srv.name", tools=[_tool("read.file")])]
    )
    route = result.routes["mcp__srv_name__read_file"]
    assert route.server_id == "srv-9"
    assert route.tool_name == "read.file"


def test_empty_inputs_produce_empty_toolset() -> None:
    assert translate_tools([]).tools == []
    assert translate_tools([]).routes == {}
    assert translate_tools([_inventory(tools=[])]).tools == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/tools/test_translate.py -v
```
Expected: collection error — `ImportError: cannot import name 'translate_tools'`.

- [ ] **Step 3: Implement** — create `backend/src/octave/tools/translate.py`:

```python
"""MCP tool schemas -> provider-native tool definitions (issue #78).

Pure translation: fleet inventories in, ProviderToolset out. No I/O, no
state, no SDK imports — octave.mcp and octave.inference never import
each other (design spec decision 5).
"""

from collections.abc import Sequence
import re

from octave.inference.types import ToolDefinition
from octave.mcp import ServerToolInventory
from octave.tools.errors import ToolNameCollisionError
from octave.tools.types import ProviderToolset, ToolRoute

__all__ = ["translate_tools"]

_NAME_PREFIX = "mcp"
_MAX_NAME_LENGTH = 64
"""OpenAI function-name charset: ^[a-zA-Z0-9_-]{1,64}$."""

_INVALID_CHARS = re.compile(r"[^a-zA-Z0-9_-]")


def _sanitize(component: str) -> str:
    """Replace characters outside the provider charset with ``_``."""
    return _INVALID_CHARS.sub("_", component)


def _exposed_name(server_name: str, tool_name: str) -> str:
    """``mcp__<server>__<tool>``, sanitized and truncated to 64 chars."""
    name = f"{_NAME_PREFIX}__{_sanitize(server_name)}__{_sanitize(tool_name)}"
    return name[:_MAX_NAME_LENGTH]


def translate_tools(
    inventories: Sequence[ServerToolInventory],
) -> ProviderToolset:
    """Translate fleet inventories into provider definitions + reverse routes.

    Deterministic: inventories in input order, tools in per-server order.
    Raises ``ToolNameCollisionError`` when two tools expose the same name.
    """
    tools: list[ToolDefinition] = []
    routes: dict[str, ToolRoute] = {}
    for inventory in inventories:
        for tool in inventory.tools:
            name = _exposed_name(inventory.server_name, tool.name)
            if name in routes:
                existing = routes[name]
                raise ToolNameCollisionError(
                    f"Tool name collision on exposed name {name!r}: "
                    f"{existing.server_id}/{existing.tool_name} and "
                    f"{inventory.server_id}/{tool.name}"
                )
            routes[name] = ToolRoute(
                server_id=inventory.server_id, tool_name=tool.name
            )
            tools.append(
                ToolDefinition(
                    name=name,
                    description=tool.description,
                    parameters=dict(tool.input_schema),
                )
            )
    return ProviderToolset(tools=tools, routes=routes)
```

Update `backend/src/octave/tools/__init__.py` — add the import and `__all__` entries:

```python
from octave.tools.errors import ToolNameCollisionError, ToolTranslationError
from octave.tools.translate import translate_tools
from octave.tools.types import ProviderToolset, ToolRoute

__all__ = [
    "ProviderToolset",
    "ToolNameCollisionError",
    "ToolRoute",
    "ToolTranslationError",
    "translate_tools",
]
```

Add `"translate_tools",` to the tuple in `backend/tests/tools/test_package.py::test_public_names_are_exported`.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/tools -v
```
Expected: all PASS (7 translate + 2 package).

- [ ] **Step 5: Lint/type gate + commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/octave/tools backend/tests/tools
git commit -m "feat(tools): translate MCP inventories to provider defs with routes"
```

---

### Task 6: Schema body — verbatim passthrough minus `$schema`, deep copy

**Files:**
- Modify: `backend/src/octave/tools/translate.py`
- Modify: `backend/tests/tools/test_translate.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/tools/test_translate.py`:

```python
def test_strips_dollar_schema_keeps_rest_verbatim() -> None:
    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "properties": {"q": {"type": "string", "enum": ["a", "b"]}},
        "required": ["q"],
        "$defs": {"nested": {"type": "object"}},
        "additionalProperties": False,
    }
    result = translate_tools([_inventory(tools=[_tool(schema=schema)])])
    parameters = result.tools[0].parameters
    assert "$schema" not in parameters
    expected = {k: v for k, v in schema.items() if k != "$schema"}
    assert parameters == expected


def test_does_not_mutate_input_schemas() -> None:
    schema = {"$schema": "draft-07", "type": "object"}
    inventory = _inventory(tools=[_tool(schema=schema)])
    result = translate_tools([inventory])
    assert inventory.tools[0].input_schema == schema  # $schema untouched
    result.tools[0].parameters["mutated"] = True
    assert "mutated" not in inventory.tools[0].input_schema


def test_description_none_omitted_from_dump() -> None:
    result = translate_tools(
        [_inventory(tools=[_tool(description=None)])]
    )
    assert "description" not in result.tools[0].model_dump(exclude_none=True)
    result = translate_tools(
        [_inventory(tools=[_tool(description="Reads files")])]
    )
    assert (
        result.tools[0].model_dump(exclude_none=True)["description"] == "Reads files"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/tools/test_translate.py -v
```
Expected: `test_strips_dollar_schema_keeps_rest_verbatim` and `test_does_not_mutate_input_schemas` FAIL (`$schema` present / shared reference mutation); `test_description_none_omitted_from_dump` passes already.

- [ ] **Step 3: Implement** — in `backend/src/octave/tools/translate.py`, add imports at the top (stdlib first per ruff/isort ordering):

```python
import copy
from typing import Any
```

Add the helper above `translate_tools`:

```python
def _parameters(schema: dict[str, Any]) -> dict[str, Any]:
    """JSON Schema passthrough minus ``$schema``; deep copy, input untouched.

    Octave never interprets JSON Schema internals (ToolInfo invariant);
    local engines (Ollama/vLLM/llama.cpp) are strict about ``$schema``.
    """
    parameters = copy.deepcopy(schema)
    parameters.pop("$schema", None)
    return parameters
```

Replace `parameters=dict(tool.input_schema),` inside `translate_tools` with:

```python
                    parameters=_parameters(tool.input_schema),
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd backend && uv run pytest tests/tools -v
```
Expected: all PASS (10 translate + 2 package).

- [ ] **Step 5: Lint/type gate + commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/octave/tools/translate.py backend/tests/tools/test_translate.py
git commit -m "feat(tools): strip \$schema and deep-copy parameters in translation"
```

(Note the escaped `$` in the commit message, or use single quotes.)

---

### Task 7: Docs + full green gate

**Files:**
- Modify: `docs/ARCHITECTURE.md` (MCP Connector section, after the Tool Execution Engine bullet ~line 72)

- [ ] **Step 1: Update `docs/ARCHITECTURE.md`** — insert a new bullet in the **MCP Connector → Responsibilities** list, immediately after the `**Tool Execution Engine**` bullet:

```markdown
- **Schema Translation** — Converts MCP `inputSchema` into the provider-native `tools` array format with `mcp__<server>__<tool>` dedupe and reverse routing *(shipped: `octave.tools.translate_tools` — pure translation into `octave.inference` `ToolDefinition`s carried on `CompletionRequest.tools`; request-side only, response-side tool-call parsing lands with the agent loop, PR #100)*
```

- [ ] **Step 2: Full test suite**

```bash
cd backend && uv run pytest -v
```
Expected: all tests pass (pre-existing suites + 14 new in `tests/tools` + 3 new adapter + 3 new types tests).

- [ ] **Step 3: Full lint/type gate**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
```
Expected: exit 0.

- [ ] **Step 4: Commit + push**

```bash
git add docs/ARCHITECTURE.md
git commit -m "docs: record tool schema translation layer in architecture"
git push
```

---

## Out of scope (recorded in the spec's deferral table)

- Response-side `tool_calls` parsing (assistant messages, stream deltas) → agent loop.
- Rename/re-describe overrides → TODO MCP Connector #9–10.
- Tool tagging → TODO #8. `tool_choice`/`parallel_tool_calls` → `extra` passthrough.
- Registry→translator glue service; collision auto-disambiguation.
