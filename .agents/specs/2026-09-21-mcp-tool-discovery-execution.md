# MCP Tool Discovery & Execution Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a fleet-wide `ToolRegistry` that caches each connected MCP server's tool inventory (`tools/list` + event-driven invalidation) and exposes `(server_id, tool_name)` invocation (`tools/call`) to the harness, per the approved spec.

**Architecture:** A new `octave/mcp/registry.py` composes on the existing `McpServerManager` public API (`status()`, `status_of()`, `get_client()`) — the manager and client gain zero changes. The registry owns an in-memory per-server inventory cache, refresh orchestration (warm-up, lazy staleness re-fetch keyed on `restart_count`, `notifications/tools/list_changed` listeners, explicit `refresh`/`refresh_all`), and a pure-forward `call_tool`. `mcp_lifespan` publishes it as `app.state.mcp_registry`; `deps.get_tool_registry` resolves it.

**Tech Stack:** Python 3.12, anyio, pydantic v2, FastAPI, pytest (asyncio auto-mode), `mcp` SDK 1.x (fixtures only — the registry never imports it).

**Spec:** [`.agents/specs/2026-09-21-mcp-tool-discovery-execution-design.md`](2026-09-21-mcp-tool-discovery-execution-design.md) — decisions 1–5 are binding.
**Branch:** `feature/mcp-tool-discovery-execution` (already checked out) · **Draft PR:** [#98](https://github.com/Svagtlys/Octave/pull/98)

## Ground rules for the implementer

- All commands run from `backend/` (e.g. `cd backend && uv run pytest ...`). `uv` is the package runner (`backend/uv.lock`).
- Tests are async automatically (`asyncio_mode = "auto"` in `pyproject.toml`) — no decorators needed on `async def test_*`.
- The quarantine rule: only `client.py`/`transport.py` import `mcp`. `registry.py` must not.
- Logging style: match `manager.py` (module logger, `key=%s` params; the pipe-format/correlation-ID pipeline lives in `middleware.py` and applies automatically — do not hand-roll correlation IDs in this package).
- Never log tool arguments (may carry user data); tool names and counts are fine.
- Commit per task, `type(scope): description` format.

## File structure

| File | Responsibility | Action |
|---|---|---|
| `backend/src/octave/mcp/registry.py` | `ServerToolInventory`, `ToolRegistry` — inventory cache, invalidation, call surface | Create |
| `backend/src/octave/mcp/deps.py` | + `get_tool_registry` resolver | Modify |
| `backend/src/octave/mcp/lifespan.py` | Build/start/stop registry, publish `app.state.mcp_registry` | Modify |
| `backend/src/octave/mcp/__init__.py` | Re-export `ToolRegistry`, `ServerToolInventory`, `get_tool_registry` | Modify |
| `backend/tests/mcp/test_registry.py` | Registry unit tests (fake manager/clients) | Create |
| `backend/tests/mcp/test_lifespan.py` | + registry publishing/shutdown tests | Modify |
| `backend/tests/test_mcp_deps.py` | + `get_tool_registry` resolver tests | Modify |
| `backend/tests/mcp/test_package.py` | + new public names | Modify |
| `backend/tests/mcp/fixtures/echo_server.py` | + `fail_tool`, `slow_tool` fixtures | Modify |
| `backend/tests/mcp/test_stdio_integration.py` | updated tool-list assertion + registry round-trip | Modify |
| `docs/TODO.md` | Check off MCP Connector #5/#6 | Modify |

Unchanged by design: `client.py`, `manager.py`, `transport.py`, `config.py`, `types.py`, `errors.py`, `app.py`. No new `McpSettings` knobs.

---

### Task 1: Commit the design doc & open the pagination follow-up issue

- [ ] **Step 1: Verify starting state**

Run: `git branch --show-current && git status --short`
Expected: `feature/mcp-tool-discovery-execution`; the design doc `.agents/specs/2026-09-21-mcp-tool-discovery-execution-design.md` present as untracked/modified.

- [ ] **Step 2: Commit the design doc**

```bash
git add .agents/specs/2026-09-21-mcp-tool-discovery-execution-design.md
git commit -m "docs(mcp): add tool discovery & execution design (#77)"
```

- [ ] **Step 3: Open the follow-up issue (spec decision 5)**

Run from the repo root:

```bash
gh issue create --title "fix(mcp): paginate tools/list in McpClient.list_tools" --body "$(cat <<'EOF'
## Problem

`McpClient.list_tools()` (backend/src/octave/mcp/client.py) sends `tools/list` once and ignores the result's `nextCursor` — servers with enough tools to paginate return a silently-truncated list.

## Fix

Loop `tools/list` passing `cursor=nextCursor` until `nextCursor` is null; concatenate pages. Add a defensive page cap (e.g. 100) that raises `McpError` on breach (guard against a server echoing the same cursor forever). Test via the in-process harness in `backend/tests/mcp/conftest.py`: a server returning two pages, assert the client returns both.

## Context

Found while scoping #77. The `ToolRegistry` (#77) deliberately assumes `list_tools()` is complete; when this lands the registry inherits correctness with zero changes.
EOF
)"
```

Expected: issue URL printed. Note it in the PR #98 body later ("Follow-up: <url>").

- [ ] **Step 4: No commit needed** (issue lives on GitHub).

---

### Task 2: `ToolRegistry` call surface (pure forward)

Smallest complete behavior: resolve a server's client through the manager and forward `tools/call`, with no cache pre-validation and no error translation (errors are the client's semantics — see spec "Execution path").

**Files:**
- Create: `backend/src/octave/mcp/registry.py`
- Create: `backend/tests/mcp/test_registry.py`

- [ ] **Step 1: Write the failing tests** — create `backend/tests/mcp/test_registry.py` with the shared fakes and call-surface tests:

```python
"""ToolRegistry unit tests — fake manager + fake clients, no SDK, no subprocess.

Fakes script the registry's view of a server, mirroring the FakeClient
pattern in tests/mcp/test_manager.py: counters + scripted outcomes, no
supervision machinery.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest

from octave.mcp.client import McpClient
from octave.mcp.errors import (
    McpConfigError,
    McpConnectionError,
    McpNotConnectedError,
    McpRpcError,
    McpTimeoutError,
)
from octave.mcp.manager import McpServerManager, ServerState, ServerStatus
from octave.mcp.registry import TOOLS_LIST_CHANGED, ToolRegistry
from octave.mcp.types import Notification, ToolContent, ToolInfo, ToolResult


def _tool(name: str) -> ToolInfo:
    return ToolInfo(name=name, description=name, input_schema={"type": "object"})


async def wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll ``predicate`` on a short in-memory sleep until it holds."""
    with anyio.fail_after(timeout):
        while not predicate():
            await anyio.sleep(0.01)


class FakeClient(McpClient):
    """McpClient stand-in scripting tools/list, tools/call, notifications."""

    def __init__(self) -> None:
        super().__init__()
        self.list_tools_calls = 0
        self.tools: list[ToolInfo] = []
        self.list_tools_error: Exception | None = None
        self.list_tools_block: anyio.Event | None = None
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.call_error: Exception | None = None
        self.call_result = ToolResult(
            content=[ToolContent(kind="text", text="ok")]
        )
        self._send, self._recv = anyio.create_memory_object_stream[Notification](
            max_buffer_size=16
        )

    async def list_tools(self) -> list[ToolInfo]:
        self.list_tools_calls += 1
        if self.list_tools_block is not None:
            await self.list_tools_block.wait()
        if self.list_tools_error is not None:
            raise self.list_tools_error
        return list(self.tools)

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> ToolResult:
        self.calls.append((name, arguments))
        if self.call_error is not None:
            raise self.call_error
        return self.call_result

    def notify(self, method: str) -> None:
        """Push an inbound notification (what the listener consumes)."""
        self._send.send_nowait(Notification(method=method))

    def subscribe_notifications(self) -> AsyncIterator[Notification]:
        async def _stream() -> AsyncIterator[Notification]:
            async for notification in self._recv:
                yield notification

        return _stream()


class FakeManager(McpServerManager):
    """Manager stand-in: scripted statuses + fake clients, no supervision."""

    def __init__(self) -> None:
        super().__init__()
        self._fakes: dict[str, tuple[ServerStatus, FakeClient]] = {}

    def add(
        self, id: str, *, name: str, state: ServerState = "connected"
    ) -> FakeClient:
        client = FakeClient()
        status = ServerStatus(
            id=id,
            name=name,
            state=state,
            transport="stdio",
            restart_count=0,
            consecutive_failures=0,
            last_error=None,
            last_state_change=datetime.now(UTC),
        )
        self._fakes[id] = (status, client)
        return client

    def set_status(self, id: str, **changes: Any) -> None:
        """Mutate one server's status (e.g. bump restart_count)."""
        status, client = self._fakes[id]
        self._fakes[id] = (replace(status, **changes), client)

    def status(self) -> list[ServerStatus]:
        return [status for status, _ in self._fakes.values()]

    def status_of(self, id: str) -> ServerStatus:
        if id not in self._fakes:
            raise McpConfigError(f"unknown server id: {id}")
        return self._fakes[id][0]

    def get_client(self, id: str) -> McpClient:
        if id not in self._fakes:
            raise McpConfigError(f"unknown server id: {id}")
        return self._fakes[id][1]


async def test_call_tool_forwards_to_named_server_client() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    registry = ToolRegistry(manager=manager)
    result = await registry.call_tool("s1", "echo", {"text": "hi"})
    assert client.calls == [("echo", {"text": "hi"})]
    assert result.content[0].text == "ok"
    assert result.is_error is False


async def test_call_tool_unknown_server_raises_config_error() -> None:
    registry = ToolRegistry(manager=FakeManager())
    with pytest.raises(McpConfigError):
        await registry.call_tool("nope", "echo")


async def test_call_tool_never_pre_validates_against_cache() -> None:
    """The server is the source of truth — a stale cache must not block calls."""
    manager = FakeManager()
    client = manager.add("s1", name="One")
    registry = ToolRegistry(manager=manager)
    await registry.call_tool("s1", "not_in_inventory")
    assert client.list_tools_calls == 0


@pytest.mark.parametrize(
    "error",
    [
        McpRpcError("bad", code=-32602),
        McpTimeoutError("slow"),
        McpConnectionError("dead"),
        McpNotConnectedError("nope"),
    ],
)
async def test_call_tool_propagates_errors_untranslated(error: Exception) -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.call_error = error
    registry = ToolRegistry(manager=manager)
    with pytest.raises(type(error)):
        await registry.call_tool("s1", "echo")


async def test_call_tool_is_error_result_passes_through() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.call_result = ToolResult(
        content=[ToolContent(kind="text", text="boom")], is_error=True
    )
    registry = ToolRegistry(manager=manager)
    result = await registry.call_tool("s1", "fail")
    assert result.is_error is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/mcp/test_registry.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'octave.mcp.registry'`.

- [ ] **Step 3: Write minimal implementation** — create `backend/src/octave/mcp/registry.py`:

```python
"""MCP tool registry — fleet-wide tool inventory + execution surface.

Role (design spec #77): the tool-plane counterpart to the manager's
lifecycle-plane. Owns the per-server inventory cache, event-driven
invalidation (restart_count drift, notifications/tools/list_changed,
explicit refresh), and the (server_id, tool_name) call surface. Composes
on ``McpServerManager``'s public API only; never imports the ``mcp`` SDK.
"""

import logging
from typing import Any

from octave.mcp.manager import McpServerManager
from octave.mcp.types import ToolResult

__all__ = ["ServerToolInventory", "ToolRegistry"]

logger = logging.getLogger(__name__)

TOOLS_LIST_CHANGED = "notifications/tools/list_changed"
"""MCP notification method signalling a server's tool-set changed."""


class ToolRegistry:
    """Fleet-wide tool inventory + execution surface over a manager."""

    def __init__(self, *, manager: McpServerManager) -> None:
        self._manager = manager

    async def call_tool(
        self, id: str, name: str, arguments: dict[str, Any] | None = None
    ) -> ToolResult:
        """Invoke a tool on one server; errors propagate untranslated.

        No cache pre-validation: the server is the source of truth, and a
        stale inventory must never block a valid call. Timeout, JSON-RPC,
        and connection semantics are the client's (``McpClient.call_tool``).
        """
        client = self._manager.get_client(id)  # unknown id -> McpConfigError
        return await client.call_tool(name, arguments=arguments)
```

(`ServerToolInventory.__all__` entry is a forward reference — Task 3 defines the class; keep `__all__` as-is, ruff does not check `__all__` contents.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/mcp/test_registry.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/registry.py backend/tests/mcp/test_registry.py
git commit -m "feat(mcp): add ToolRegistry call surface forwarding to managed clients"
```

---

### Task 3: Discovery — `refresh` + lazy fallback reads with last-known-good

The inventory data model plus read semantics (spec decision 4): unpopulated → synchronous fetch; failure → last-known-good kept, `last_error` recorded, `fetched_at` left unset so the next read retries; reads never raise on I/O failure.

**Files:**
- Modify: `backend/src/octave/mcp/registry.py`
- Modify: `backend/tests/mcp/test_registry.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/mcp/test_registry.py`:

```python
async def test_tools_for_unpopulated_fetches_synchronously() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    tools = await registry.tools_for("s1")
    assert [t.name for t in tools] == ["echo"]
    assert client.list_tools_calls == 1


async def test_inventory_reports_freshness_metadata() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    [inv] = await registry.inventory()
    assert inv.server_id == "s1"
    assert inv.server_name == "One"
    assert inv.state == "connected"
    assert inv.fetched_at is not None
    assert inv.last_error is None


async def test_discovery_failure_records_error_and_never_raises() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.list_tools_error = McpTimeoutError("boom")
    registry = ToolRegistry(manager=manager)
    tools = await registry.tools_for("s1")
    assert tools == []
    [inv] = await registry.inventory()
    assert inv.fetched_at is None  # failure does not mark the entry fresh
    assert inv.last_error == "boom"


async def test_last_known_good_survives_later_failure() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    await registry.tools_for("s1")
    first_fetched_at = registry_status_fetched_at(registry)
    client.tools = []
    client.list_tools_error = McpConnectionError("died")
    manager.set_status("s1", restart_count=1)  # force staleness
    [inv] = await registry.inventory()
    assert [t.name for t in inv.tools] == ["echo"]  # kept, not wiped
    assert inv.last_error == "died"
    assert inv.fetched_at == first_fetched_at  # timestamp of last SUCCESS


def registry_status_fetched_at(registry: ToolRegistry):  # noqa: ANN201 - test helper
    """Grab the entry's fetched_at via the (sync) internal cache."""
    return next(iter(registry._entries.values())).fetched_at  # noqa: SLF001


async def test_recovered_server_clears_error_on_next_read() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.list_tools_error = McpTimeoutError("boom")
    registry = ToolRegistry(manager=manager)
    await registry.tools_for("s1")
    client.list_tools_error = None
    client.tools = [_tool("echo")]
    [inv] = await registry.inventory()  # still stale (never succeeded) -> retry
    assert inv.last_error is None
    assert [t.name for t in inv.tools] == ["echo"]


async def test_refresh_unknown_server_raises_config_error() -> None:
    registry = ToolRegistry(manager=FakeManager())
    with pytest.raises(McpConfigError):
        await registry.refresh("nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/mcp/test_registry.py -v`
Expected: new tests FAIL with `AttributeError` (`tools_for`/`inventory`/`refresh`/`_entries` missing); Task 2 tests still PASS.

- [ ] **Step 3: Write the implementation** — replace `backend/src/octave/mcp/registry.py` entirely:

```python
"""MCP tool registry — fleet-wide tool inventory + execution surface.

Role (design spec #77): the tool-plane counterpart to the manager's
lifecycle-plane. Owns the per-server inventory cache, event-driven
invalidation (restart_count drift, notifications/tools/list_changed,
explicit refresh), and the (server_id, tool_name) call surface. Composes
on ``McpServerManager``'s public API only; never imports the ``mcp`` SDK.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anyio
from pydantic import BaseModel

from octave.mcp.errors import McpError
from octave.mcp.manager import McpServerManager, ServerState
from octave.mcp.types import ToolInfo, ToolResult

__all__ = ["ServerToolInventory", "ToolRegistry"]

logger = logging.getLogger(__name__)

TOOLS_LIST_CHANGED = "notifications/tools/list_changed"
"""MCP notification method signalling a server's tool-set changed."""


class ServerToolInventory(BaseModel):
    """One server's cached tool inventory + freshness metadata."""

    server_id: str
    server_name: str
    state: ServerState
    tools: list[ToolInfo]
    """Last-known-good discovery result; empty until the first success."""

    fetched_at: datetime | None = None
    """Start of the successful discovery that produced ``tools``."""

    last_error: str | None = None
    """Most recent discovery failure (Octave exception message, redacted)."""


@dataclass
class _CacheEntry:
    """Internal cache state for one server's inventory."""

    tools: list[ToolInfo] = field(default_factory=list)
    fetched_at: datetime | None = None
    last_error: str | None = None
    restart_count_at_fetch: int = -1
    lock: anyio.Lock = field(default_factory=anyio.Lock)


class ToolRegistry:
    """Fleet-wide tool inventory + execution surface over a manager.

    Safe for concurrent use: cache mutations happen under per-server locks;
    reads snapshot plain attributes (the manager's lock-free-read posture).
    """

    def __init__(self, *, manager: McpServerManager) -> None:
        self._manager = manager
        self._entries: dict[str, _CacheEntry] = {}

    # ---- discovery --------------------------------------------------

    async def refresh(self, id: str) -> None:
        """Re-run tools/list for one server; never raises on I/O failure.

        Unknown id -> ``McpConfigError`` (the only raise). Discovery failure
        keeps last-known-good tools and records ``last_error`` (spec
        decision 4: read paths must stay safe).
        """
        self._manager.status_of(id)  # unknown id -> McpConfigError
        entry = self._entry(id)
        async with entry.lock:
            await self._refresh_locked(id)

    async def _refresh_locked(self, id: str) -> None:
        """One discovery round; caller must hold the server lock."""
        entry = self._entry(id)
        try:
            tools = await self._manager.get_client(id).list_tools()
        except McpError as exc:
            # Last-known-good posture: keep stale tools, record the
            # failure, leave fetched_at untouched so the next read retries.
            entry.last_error = str(exc)
            logger.exception("MCP tool discovery failed | id=%s error=%s", id, exc)
            return
        entry.tools = tools
        entry.fetched_at = datetime.now(UTC)
        entry.restart_count_at_fetch = self._manager.status_of(id).restart_count
        entry.last_error = None
        logger.info("MCP tool inventory refreshed | id=%s tools=%s", id, len(tools))

    # ---- reads ------------------------------------------------------

    async def inventory(self) -> list[ServerToolInventory]:
        """Snapshot of every registered server, registration order.

        Stale/unpopulated entries are refreshed synchronously first.
        """
        return [await self._read(s.id) for s in self._manager.status()]

    async def tools_for(self, id: str) -> list[ToolInfo]:
        """One server's tools; refreshed synchronously when stale."""
        inventory = await self._read(id)
        return list(inventory.tools)

    async def _read(self, id: str) -> ServerToolInventory:
        entry = self._entry(id)
        if self._is_stale(id, entry):
            async with entry.lock:
                if self._is_stale(id, entry):  # recheck: concurrent reads coalesce
                    await self._refresh_locked(id)
        status = self._manager.status_of(id)  # unknown id -> McpConfigError
        return ServerToolInventory(
            server_id=id,
            server_name=status.name,
            state=status.state,
            tools=list(entry.tools),
            fetched_at=entry.fetched_at,
            last_error=entry.last_error,
        )

    def _is_stale(self, id: str, entry: _CacheEntry) -> bool:
        """Never fetched, or the server restarted since the fetch.

        restart_count drift detects connect/restart/crash-recovery without
        a manager hook — the manager stays "policy only" (#18 spec).
        """
        if entry.fetched_at is None:
            return True
        return (
            self._manager.status_of(id).restart_count
            != entry.restart_count_at_fetch
        )

    # ---- execution --------------------------------------------------

    async def call_tool(
        self, id: str, name: str, arguments: dict[str, Any] | None = None
    ) -> ToolResult:
        """Invoke a tool on one server; errors propagate untranslated.

        No cache pre-validation: the server is the source of truth, and a
        stale inventory must never block a valid call. Timeout, JSON-RPC,
        and connection semantics are the client's (``McpClient.call_tool``).
        """
        client = self._manager.get_client(id)  # unknown id -> McpConfigError
        return await client.call_tool(name, arguments=arguments)

    def _entry(self, id: str) -> _CacheEntry:
        return self._entries.setdefault(id, _CacheEntry())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/mcp/test_registry.py -v`
Expected: all PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/registry.py backend/tests/mcp/test_registry.py
git commit -m "feat(mcp): add inventory cache with lazy fallback and last-known-good failures"
```

---

### Task 4: Staleness by `restart_count` + read coalescing under the lock

**Files:**
- Modify: `backend/tests/mcp/test_registry.py` (implementation already landed in Task 3 — this task pins the two behaviors that the lock and drift check exist for)

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/mcp/test_registry.py`:

```python
async def test_fresh_entry_is_served_from_cache_without_wire_traffic() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    await registry.tools_for("s1")
    await registry.tools_for("s1")
    await registry.inventory()
    assert client.list_tools_calls == 1


async def test_restart_invalidates_entry() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    await registry.tools_for("s1")
    manager.set_status("s1", restart_count=1)
    await registry.tools_for("s1")
    assert client.list_tools_calls == 2


async def test_concurrent_stale_reads_coalesce_into_one_fetch() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    client.list_tools_block = anyio.Event()
    registry = ToolRegistry(manager=manager)

    results: list[int] = []

    async def reader() -> None:
        tools = await registry.tools_for("s1")
        results.append(len(tools))

    async with anyio.create_task_group() as tg:
        for _ in range(5):
            tg.start_soon(reader)
        await anyio.sleep(0.05)  # one reader holds the lock inside list_tools
        client.list_tools_block.set()

    assert client.list_tools_calls == 1
    assert results == [1] * 5
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/mcp/test_registry.py -v`
Expected: all PASS — Task 3's `_is_stale`/lock already implement this; these tests are the behavioral pin. If any fail, fix `registry.py` (re-check inside the lock is the load-bearing line) before committing.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/mcp/test_registry.py
git commit -m "test(mcp): pin restart-drift invalidation and read coalescing"
```

---

### Task 5: `start()`/`stop()` — warm-up task and `tools/list_changed` listeners

**Files:**
- Modify: `backend/src/octave/mcp/registry.py`
- Modify: `backend/tests/mcp/test_registry.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/mcp/test_registry.py`:

```python
async def test_warm_up_waits_for_connection_then_populates() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One", state="starting")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    await registry.start()
    try:
        await anyio.sleep(0.05)
        assert client.list_tools_calls == 0  # still starting: zero traffic
        manager.set_status("s1", state="connected")
        await wait_for(lambda: client.list_tools_calls == 1)
    finally:
        await registry.stop()


async def test_list_changed_notification_triggers_refresh() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    await registry.start()
    try:
        await wait_for(lambda: client.list_tools_calls == 1)  # warm-up fetch
        client.notify(TOOLS_LIST_CHANGED)
        await wait_for(lambda: client.list_tools_calls == 2)
    finally:
        await registry.stop()


async def test_unrelated_notifications_do_not_refresh() -> None:
    manager = FakeManager()
    client = manager.add("s1", name="One")
    client.tools = [_tool("echo")]
    registry = ToolRegistry(manager=manager)
    await registry.start()
    try:
        await wait_for(lambda: client.list_tools_calls == 1)
        client.notify("notifications/message")
        await anyio.sleep(0.05)
        assert client.list_tools_calls == 1
    finally:
        await registry.stop()


async def test_stop_is_idempotent() -> None:
    manager = FakeManager()
    manager.add("s1", name="One")
    registry = ToolRegistry(manager=manager)
    await registry.start()
    await registry.stop()
    await registry.stop()  # must not raise or hang
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/mcp/test_registry.py -v`
Expected: the four new tests FAIL with `AttributeError: 'ToolRegistry' object has no attribute 'start'`.

- [ ] **Step 3: Write the implementation** — in `backend/src/octave/mcp/registry.py`:

Add imports at the top (merge with existing):

```python
import anyio.abc
```

Add module constant after `TOOLS_LIST_CHANGED`:

```python
_WARM_UP_POLL_SECONDS = 0.1
"""In-memory status-poll interval while waiting for servers to settle."""
```

Add a lifecycle section to `ToolRegistry` (place before the discovery section), and add the `_task_group` field in `__init__` (`self._task_group: anyio.abc.TaskGroup | None = None` after `self._entries`):

```python
    # ---- lifecycle --------------------------------------------------

    async def start(self) -> None:
        """Spawn per-server list_changed listeners + one warm-up task.

        Manual task-group entry with the manager's same-task convention —
        ``mcp_lifespan`` calls start/stop from the lifespan task.
        """
        if self._task_group is not None:
            raise McpError("ToolRegistry.start() already called")
        self._task_group = anyio.create_task_group()
        await self._task_group.__aenter__()
        for status in self._manager.status():
            self._task_group.start_soon(self._listen_tools_changed, status.id)
        self._task_group.start_soon(self._warm_up)

    async def stop(self) -> None:
        """Cancel listener/warm-up tasks; awaited. Idempotent.

        Cancellation is safe here (unlike the manager's cooperative stop):
        these tasks own no SDK cancel scopes — they only consume Octave
        memory streams, whose aclose runs during cancellation unwinding.
        """
        if self._task_group is None:
            return
        task_group, self._task_group = self._task_group, None
        task_group.cancel_scope.cancel()
        await task_group.__aexit__(None, None, None)

    async def _warm_up(self) -> None:
        """Populate each server's inventory once its supervisor settles.

        Polls in-memory manager state (zero MCP traffic) until each server
        reaches ``connected``/``crashed``, then refreshes it. Runs as a
        background task so boot is never blocked (spec decision 4).
        """
        for status in self._manager.status():
            while True:
                state = self._manager.status_of(status.id).state
                if state in ("connected", "crashed"):
                    break
                await anyio.sleep(_WARM_UP_POLL_SECONDS)
            await self.refresh(status.id)

    async def _listen_tools_changed(self, id: str) -> None:
        """Refresh the inventory when the server announces tool changes.

        The subscription stream lives on the client instance, which the
        manager retains across restarts (aclose never tears down
        notification senders) — restart-safe by construction. Failures are
        contained: the lazy staleness path is the backstop.
        """
        try:
            client = self._manager.get_client(id)
            async for notification in client.subscribe_notifications():
                if notification.method == TOOLS_LIST_CHANGED:
                    await self.refresh(id)
        except Exception:
            logger.exception("MCP list_changed listener failed | id=%s", id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/mcp/test_registry.py -v`
Expected: all PASS (21 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/registry.py backend/tests/mcp/test_registry.py
git commit -m "feat(mcp): add registry warm-up and tools/list_changed listeners"
```

---

### Task 6: `refresh_all`

**Files:**
- Modify: `backend/src/octave/mcp/registry.py`
- Modify: `backend/tests/mcp/test_registry.py`

- [ ] **Step 1: Write the failing test** — append to `backend/tests/mcp/test_registry.py`:

```python
async def test_refresh_all_covers_every_server_and_isolates_failures() -> None:
    manager = FakeManager()
    good = manager.add("s1", name="One")
    good.tools = [_tool("echo")]
    bad = manager.add("s2", name="Two")
    bad.list_tools_error = McpTimeoutError("boom")
    registry = ToolRegistry(manager=manager)
    await registry.refresh_all()
    inventories = {inv.server_id: inv for inv in await registry.inventory()}
    assert [t.name for t in inventories["s1"].tools] == ["echo"]
    assert inventories["s1"].last_error is None
    assert inventories["s2"].last_error == "boom"  # failure isolated, s1 intact
    assert good.list_tools_calls >= 1
    assert bad.list_tools_calls >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/mcp/test_registry.py::test_refresh_all_covers_every_server_and_isolates_failures -v`
Expected: FAIL with `AttributeError: ... no attribute 'refresh_all'`.

- [ ] **Step 3: Write the implementation** — add to the discovery section of `ToolRegistry`, after `refresh`:

```python
    async def refresh_all(self) -> None:
        """Refresh every registered server concurrently (per-server locks)."""
        async with anyio.create_task_group() as task_group:
            for status in self._manager.status():
                task_group.start_soon(self.refresh, status.id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/mcp/test_registry.py -v`
Expected: all PASS (22 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/registry.py backend/tests/mcp/test_registry.py
git commit -m "feat(mcp): add ToolRegistry.refresh_all"
```

---

### Task 7: `get_tool_registry` dependency resolver

**Files:**
- Modify: `backend/src/octave/mcp/deps.py`
- Modify: `backend/tests/test_mcp_deps.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/test_mcp_deps.py`:

```python
from octave.mcp.deps import get_mcp_manager, get_tool_registry
from octave.mcp.manager import McpServerManager
from octave.mcp.registry import ToolRegistry


def _registry_probe_app() -> FastAPI:
    """A minimal app whose only route depends on the registry."""
    app = FastAPI()

    @app.get("/probe")
    async def _probe(
        registry: ToolRegistry = Depends(get_tool_registry),
    ) -> dict[str, str]:
        return {"registry": type(registry).__name__}

    return app


def test_registry_unset_returns_503() -> None:
    response = TestClient(_registry_probe_app()).get("/probe")
    assert response.status_code == 503
    assert response.json()["detail"] == "MCP tool registry not configured"


def test_registry_state_provides() -> None:
    app = _registry_probe_app()
    app.state.mcp_registry = ToolRegistry(manager=McpServerManager())
    response = TestClient(app).get("/probe")
    assert response.status_code == 200
    assert response.json() == {"registry": "ToolRegistry"}


def test_registry_dependency_override_wins() -> None:
    class FakeRegistry(ToolRegistry):
        """No-op stand-in proving routes resolve through the override seam."""

    app = _registry_probe_app()
    app.dependency_overrides[get_tool_registry] = lambda: FakeRegistry(
        manager=McpServerManager()
    )
    response = TestClient(app).get("/probe")
    assert response.status_code == 200
    assert response.json() == {"registry": "FakeRegistry"}
```

Note: the file already imports `Depends`, `FastAPI`, `TestClient`, `get_mcp_manager`, `McpServerManager` at the top — merge the new imports into the existing import block (replace the two existing import lines for `get_mcp_manager`/`McpServerManager` with the block above; do not duplicate).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_mcp_deps.py -v`
Expected: collection error — `ImportError: cannot import name 'get_tool_registry'`.

- [ ] **Step 3: Write the implementation** — in `backend/src/octave/mcp/deps.py`, update imports and append the resolver:

```python
from octave.mcp.manager import McpServerManager
from octave.mcp.registry import ToolRegistry

__all__ = ["get_mcp_manager", "get_tool_registry"]
```

```python
async def get_tool_registry(request: Request) -> ToolRegistry:
    """Resolve the app-wide tool registry from ``app.state.mcp_registry``.

    Raises 503 while no registry is configured — Octave boots fine without
    any MCP servers (same posture as get_mcp_manager).
    """
    registry: ToolRegistry | None = getattr(request.app.state, "mcp_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=503, detail="MCP tool registry not configured"
        )
    return registry
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_mcp_deps.py -v`
Expected: 7 PASS (4 existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/deps.py backend/tests/test_mcp_deps.py
git commit -m "feat(mcp): add get_tool_registry dependency resolver"
```

---

### Task 8: Lifespan wiring — build, start, publish, stop

**Files:**
- Modify: `backend/src/octave/mcp/lifespan.py`
- Modify: `backend/tests/mcp/test_lifespan.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/mcp/test_lifespan.py` (merge `ToolRegistry` into the import block):

```python
from octave.mcp.registry import ToolRegistry


def test_startup_publishes_tool_registry() -> None:
    factory = FakeFactory()
    app = _app_with(factory)
    with TestClient(app) as client:
        assert isinstance(client.app.state.mcp_registry, ToolRegistry)


def test_registry_stops_cleanly_on_shutdown() -> None:
    """Shutdown must not hang: registry listeners cancelled before aclose."""
    factory = FakeFactory()
    app = _app_with(factory)
    with TestClient(app):
        pass
    assert factory.clients[0].aclose_calls >= 1


def test_default_configs_publish_empty_registry() -> None:
    app = FastAPI(lifespan=mcp_lifespan)
    with TestClient(app) as client:
        registry = client.app.state.mcp_registry
        assert isinstance(registry, ToolRegistry)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/mcp/test_lifespan.py -v`
Expected: new tests FAIL with `AttributeError` (no `mcp_registry` on state).

- [ ] **Step 3: Write the implementation** — in `backend/src/octave/mcp/lifespan.py`, add the import `from octave.mcp.registry import ToolRegistry` and replace the body of `mcp_lifespan` (from `entries = list(configs)` through the final log line) with:

```python
    entries = list(configs)
    manager = McpServerManager(client_factory=client_factory)
    for server_id, name, config in entries:
        manager.register(id=server_id, name=name, config=config)
    await manager.start_all()
    app.state.mcp_manager = manager
    registry = ToolRegistry(manager=manager)
    await registry.start()
    app.state.mcp_registry = registry
    logger.info("MCP manager ready: %s server(s)", len(entries))
    try:
        yield
    finally:
        # Registry teardown first: listeners stop consuming streams before
        # connections unwind.
        await registry.stop()
        await manager.stop_all()
        logger.info("MCP manager stopped")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/mcp/test_lifespan.py -v`
Expected: 6 PASS (3 existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/lifespan.py backend/tests/mcp/test_lifespan.py
git commit -m "feat(mcp): wire ToolRegistry into mcp_lifespan as app.state.mcp_registry"
```

---

### Task 9: Package re-exports

**Files:**
- Modify: `backend/src/octave/mcp/__init__.py`
- Modify: `backend/tests/mcp/test_package.py`

- [ ] **Step 1: Write the failing test** — in `backend/tests/mcp/test_package.py`, add `"ToolRegistry"`, `"ServerToolInventory"`, `"get_tool_registry"` to the tuple in `test_public_names_are_exported`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/mcp/test_package.py -v`
Expected: FAIL — `assert hasattr(mcp_pkg, 'ToolRegistry')`.

- [ ] **Step 3: Write the implementation** — in `backend/src/octave/mcp/__init__.py`: update the `deps` import line to `from octave.mcp.deps import get_mcp_manager, get_tool_registry`; add `from octave.mcp.registry import ServerToolInventory, ToolRegistry` (after the manager import, keeping alphabetical module order); add `"ServerToolInventory"`, `"ToolRegistry"`, `"get_tool_registry"` to `__all__` (alphabetical: `"ServerToolInventory"` after `"ServerStatus"`, `"ToolRegistry"` after `"ToolInfo"`, `"get_tool_registry"` after `"get_mcp_manager"`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/mcp/test_package.py -v`
Expected: 2 PASS (quarantine test still passes — `registry.py` imports no SDK symbols).

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/__init__.py backend/tests/mcp/test_package.py
git commit -m "feat(mcp): export ToolRegistry, ServerToolInventory, get_tool_registry"
```

---

### Task 10: Integration — fixture tools + real-subprocess registry round-trip

**Files:**
- Modify: `backend/tests/mcp/fixtures/echo_server.py`
- Modify: `backend/tests/mcp/test_stdio_integration.py`

- [ ] **Step 1: Extend the fixture server** — replace `backend/tests/mcp/fixtures/echo_server.py` entirely:

```python
"""Minimal stdio MCP server for the integration test.

Spawned by tests/mcp/test_stdio_integration.py via StdioConfig — exercises
the real SDK stdio_client path (subprocess pipes, newline-delimited JSON).
"""

import time

from mcp.server.fastmcp import FastMCP

server = FastMCP("echo-fixture")


@server.tool()
def echo(text: str) -> str:
    """Echo the input text back."""
    return text


@server.tool()
def fail_tool() -> str:
    """Raise — FastMCP converts the exception into an isError=True result."""
    raise RuntimeError("fail_tool exploded")


@server.tool()
def slow_tool() -> str:
    """Block for 5 s — the client-timeout fixture."""
    time.sleep(5)
    return "too late"


if __name__ == "__main__":
    server.run()
```

- [ ] **Step 2: Update the existing tool-list assertion** — in `backend/tests/mcp/test_stdio_integration.py:36`, change:

```python
        assert [tool.name for tool in tools] == ["echo"]
```

to:

```python
        assert [tool.name for tool in tools] == ["echo", "fail_tool", "slow_tool"]
```

(FastMCP preserves registration order.)

- [ ] **Step 3: Write the registry round-trip test** — append to `backend/tests/mcp/test_stdio_integration.py` (add `McpTimeoutError` to the existing `from octave.mcp.errors import ...` line):

```python
async def test_registry_discovers_and_executes_tools_over_stdio() -> None:
    """Manager + registry over a real subprocess: discover, call, fail, time out."""
    from octave.mcp.manager import McpServerManager
    from octave.mcp.registry import ToolRegistry
    from tests.mcp.test_manager import wait_for

    settings = _McpSettings(request_timeout_seconds=0.5)
    manager = McpServerManager(settings=settings)
    manager.register(
        id="echo",
        name="Echo",
        config=StdioConfig(command=sys.executable, args=[str(_ECHO_SERVER)]),
    )
    await manager.start_all()
    registry = ToolRegistry(manager=manager)
    await registry.start()
    try:
        await wait_for(manager, "echo", lambda s: s.state == "connected", timeout=15)
        tools = await registry.tools_for("echo")
        assert [tool.name for tool in tools] == ["echo", "fail_tool", "slow_tool"]

        result = await registry.call_tool("echo", "echo", {"text": "registry"})
        assert result.content[0].text == "registry"
        assert result.is_error is False

        failed = await registry.call_tool("echo", "fail_tool")
        assert failed.is_error is True

        with pytest.raises(McpTimeoutError):
            await registry.call_tool("echo", "slow_tool")
    finally:
        await registry.stop()
        await manager.stop_all()
```

Note: `McpSettings` is already imported at module top as `McpSettings` — use it directly instead of `_McpSettings` (the underscore alias exists only inside other tests here); write `settings = McpSettings(request_timeout_seconds=0.5)`.

- [ ] **Step 4: Run the integration suite**

Run: `cd backend && uv run pytest tests/mcp/test_stdio_integration.py -v`
Expected: 5 PASS (4 existing + 1 new). The slow-tool timeout also fires `on_lost("timeout")` → the manager may probe/restart in the background; the test completes and tears down before that matters. If the sandbox forbids subprocesses, `OCTAVE_MCP_SKIP_SUBPROCESS_TESTS=1` skips the file — run the rest of the suite instead and note the skip in the PR.

- [ ] **Step 5: Commit**

```bash
git add backend/tests/mcp/fixtures/echo_server.py backend/tests/mcp/test_stdio_integration.py
git commit -m "test(mcp): registry discovery/execution round-trip over real subprocess"
```

---

### Task 11: Full verification + docs check-off

**Files:**
- Modify: `docs/TODO.md`

- [ ] **Step 1: Run the full backend suite**

Run: `cd backend && uv run pytest -q`
Expected: all PASS, zero skips except the conditional subprocess skip guard (report any skip explicitly).

- [ ] **Step 2: Lint and type-check**

Run: `cd backend && uv run ruff check . && uv run mypy src`
Expected: `All checks passed!` and no mypy errors. Fix any issues (unused imports in `__init__.py` are the usual suspects) and re-run.

- [ ] **Step 3: Check off the roadmap items** — in `docs/TODO.md`, replace lines 79–80:

```markdown
- [ ] 5. Implement tool discovery and caching (fetch tools, schemas, descriptions)
- [ ] 6. Create tool execution engine (invoke tools, handle responses/errors, timeouts)
```

with:

```markdown
- [x] 5. Implement tool discovery and caching (fetch tools, schemas, descriptions) — PR #98 (ToolRegistry inventory; event-driven invalidation; cursor pagination deferred to follow-up issue)
- [x] 6. Create tool execution engine (invoke tools, handle responses/errors, timeouts) — PR #98 ((server_id, tool_name) surface over McpClient.call_tool)
```

- [ ] **Step 4: Commit**

```bash
git add docs/TODO.md
git commit -m "docs: check off MCP Connector tool discovery & execution items (#77)"
```

- [ ] **Step 5: Push**

```bash
git push
```

PR-body hygiene (follow-up issue URL from Task 1, spec/plan links) belongs to the `finish-work-item` skill — record the follow-up issue URL in the final completion message so it carries over.

---

## Self-review notes (author)

- **Spec coverage:** decisions 1–5 map to Tasks 2–6 (registry module, event-driven invalidation, addressing, eager+lazy reads, pagination deferred via Task 1); wiring (spec "Wiring") → Tasks 7–9; testing section → Tasks 2–6, 8, 10. No spec requirement left without a task.
- **Type consistency:** `ServerToolInventory` fields, `ToolRegistry` method names, `TOOLS_LIST_CHANGED`, and `FakeManager`/`FakeClient` signatures are defined once (Tasks 2–3/5) and reused verbatim in later tasks.
- **Known deliberate smells:** `registry_status_fetched_at` reaches into `registry._entries` (white-box peek at a private cache to pin `fetched_at` on the success path — the public inventory snapshot is already covered elsewhere); `except Exception` in the listener is containment with logging, not silence (coding rules forbid *bare* except).
