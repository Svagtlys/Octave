# MCP Server Lifecycle Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `McpServerManager` — multi-server supervision with auto-restart (exponential backoff + crash-loop detection), probe-on-timeout health confirmation, and app lifespan wiring, so agents have a reliable view of which MCP servers are available.

**Architecture:** The manager holds one `McpClient` per registered server plus a per-server supervisor task in one `anyio.TaskGroup`. The supervisor is the sole caller of its client's lifecycle methods (`connect`/`aclose`/`ping`), honoring `McpClient.restart()`'s single-caller contract by construction. The client gains one additive `on_lost(reason)` hook ("death" | "timeout"); the manager confirms suspected hangs with a short-timeout probe-ping before restarting. Configs arrive via a registration API — no DB reads (roadmap #7 owns that).

**Tech Stack:** Python 3.12, anyio (task groups, cancel scopes, events), pydantic-settings, FastAPI lifespan, pytest + pytest-asyncio (auto mode), ruff, mypy --strict.

**Spec:** [`.agents/specs/2026-09-18-mcp-lifecycle-manager-design.md`](./2026-09-18-mcp-lifecycle-manager-design.md) — decisions 1–6 are settled; do not re-litigate.

**Branch:** `feature/mcp-server-lifecycle-manager` · **Draft PR:** [#93](https://github.com/Svagtlys/Octave/pull/93)

**Commands:** run everything from `backend/` with `uv run …` (e.g. `cd backend && uv run pytest tests/mcp -q`). `pytest` config lives in `pyproject.toml` (`asyncio_mode = "auto"` — plain `async def` tests need no decorator).

**One deliberate deviation from the spec's illustrative signature:** the spec shows `client_factory: Callable[[], McpClient]`, but the factory must wire the per-server `on_lost` hook, so the real signature is `Callable[[Callable[[str], None]], McpClient]` (hook in, client out). The default factory builds `McpClient(settings=…, on_lost=hook)`.

---

## File map

| File | Responsibility |
|---|---|
| `src/octave/mcp/config.py` | +5 supervision settings on `McpSettings` |
| `src/octave/mcp/client.py` | additive `on_lost` constructor hook + `_fire_lost` (two fire sites) |
| `src/octave/mcp/manager.py` | **new** — `ServerState`, `ServerStatus`, `McpServerManager`, supervisor loop |
| `src/octave/mcp/lifespan.py` | **new** — `mcp_lifespan` (build → register → start_all → publish → stop_all) |
| `src/octave/mcp/deps.py` | `get_mcp_client` removed; `get_mcp_manager` added |
| `src/octave/mcp/__init__.py` | re-exports updated |
| `src/octave/app.py` | `compose_lifespans` helper; app lifespan = db + mcp |
| `tests/mcp/conftest.py` | `harness_cm` gains `on_lost` passthrough |
| `tests/mcp/test_config.py` | settings defaults test |
| `tests/mcp/test_client.py` | `on_lost` seam tests |
| `tests/mcp/test_manager.py` | **new** — supervision unit tests (fake clients) |
| `tests/mcp/test_lifespan.py` | **new** — lifespan + compose tests |
| `tests/test_mcp_deps.py` | rewritten for `get_mcp_manager` |
| `tests/mcp/test_stdio_integration.py` | manager auto-restart + crash-loop against real subprocesses |
| `docs/ARCHITECTURE.md`, `docs/TODO.md` | status updates |

---

### Task 1: Supervision settings

**Files:**
- Modify: `src/octave/mcp/config.py` (end of `McpSettings`, after `request_timeout_seconds: float = 30.0`)
- Test: `tests/mcp/test_config.py`

- [ ] **Step 1: Write the failing test** — append to `tests/mcp/test_config.py` (import `McpSettings` at top if not already imported):

```python
class TestSupervisionSettings:
    """Lifecycle-manager knobs (spec: restart policy table)."""

    def test_defaults(self) -> None:
        settings = McpSettings()
        assert settings.restart_base_delay_seconds == 1.0
        assert settings.restart_max_delay_seconds == 60.0
        assert settings.restart_max_attempts == 5
        assert settings.probe_timeout_seconds == 5.0
        assert settings.stabilization_seconds == 60.0
```

- [ ] **Step 2: Run it, verify failure**

Run: `cd backend && uv run pytest tests/mcp/test_config.py::TestSupervisionSettings -q`
Expected: FAIL — `AttributeError: 'McpSettings' object has no attribute 'restart_base_delay_seconds'`

- [ ] **Step 3: Implement** — in `src/octave/mcp/config.py`, extend `McpSettings` below `request_timeout_seconds`:

```python
    request_timeout_seconds: float = 30.0

    restart_base_delay_seconds: float = 1.0
    """First auto-restart delay; exponential backoff multiplies by 2 per attempt."""

    restart_max_delay_seconds: float = 60.0
    """Cap on the backoff delay."""

    restart_max_attempts: int = 5
    """Consecutive failed restart cycles before a server enters 'crashed'."""

    probe_timeout_seconds: float = 5.0
    """Budget for the manager's confirming ping after a request timeout."""

    stabilization_seconds: float = 60.0
    """Time a connection must hold before the failure counter resets."""
```

- [ ] **Step 4: Run it, verify pass**

Run: `cd backend && uv run pytest tests/mcp/test_config.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/config.py backend/tests/mcp/test_config.py
git commit -m "feat(mcp): add supervision settings for the lifecycle manager"
```

---

### Task 2: `on_lost` hook on `McpClient`

**Files:**
- Modify: `src/octave/mcp/client.py` (`__init__` ~lines 148–164; `_on_transport_death` ~lines 217–230; `_run` timeout branch ~lines 476–479)
- Modify: `tests/mcp/conftest.py` (`harness_cm` ~lines 136–155)
- Test: `tests/mcp/test_client.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/mcp/test_client.py` (follow the file's existing `async with harness() as (client, peer)` style; the `on_lost` kwarg on the harness is added in Step 3b):

```python
async def test_on_lost_fires_death_when_server_process_dies(harness) -> None:
    reasons: list[str] = []
    async with harness(on_lost=reasons.append) as (_client, peer):
        await peer.swrite.aclose()  # read stream dies → monitor fires
        await anyio.sleep(0.05)
    assert reasons == ["death"]


async def test_on_lost_fires_timeout_on_request_timeout(harness) -> None:
    reasons: list[str] = []
    async with harness(request_timeout=0.1, on_lost=reasons.append) as (client, _peer):
        with pytest.raises(McpTimeoutError):
            await client.call_tool("slow")  # harness 'slow' sleeps 30s
    assert reasons == ["timeout"]


async def test_on_lost_not_fired_on_success(harness) -> None:
    reasons: list[str] = []
    async with harness(on_lost=reasons.append) as (client, _peer):
        await client.call_tool("echo", {"text": "hi"})
    assert reasons == []


async def test_on_lost_hook_exception_never_reaches_caller(harness) -> None:
    def boom(_reason: str) -> None:
        raise RuntimeError("hook bug")

    async with harness(request_timeout=0.1, on_lost=boom) as (client, _peer):
        with pytest.raises(McpTimeoutError):  # the Octave error, not RuntimeError
            await client.call_tool("slow")
```

- [ ] **Step 2: Run them, verify failure**

Run: `cd backend && uv run pytest tests/mcp/test_client.py -k on_lost -q`
Expected: FAIL — `TypeError: McpClient.__init__() got an unexpected keyword argument 'on_lost'` (and the harness rejects `on_lost`)

- [ ] **Step 3a: Implement the client seam** — in `src/octave/mcp/client.py`:

Replace the `__init__` header (keep all existing body assignments, add `self._on_lost = on_lost`):

```python
    def __init__(
        self,
        *,
        transport_factory: TransportFactory = open_transport,
        settings: McpSettings | None = None,
        on_lost: Callable[[str], None] | None = None,
    ) -> None:
        self._transport_factory = transport_factory
        self._settings = settings or McpSettings()
        self._on_lost = on_lost
        # …remaining existing assignments unchanged…
```

Add the method (place near `_on_transport_death`):

```python
    def _fire_lost(self, reason: str) -> None:
        """Notify the manager of a lost connection; never break the client.

        ``reason`` is ``"death"`` (transport died) or ``"timeout"`` (a request
        timed out — the server may be wedged). Synchronous; exceptions from
        the hook are suppressed so a manager bug can never corrupt the
        client's own error reporting.
        """
        if self._on_lost is None:
            return
        try:
            self._on_lost(reason)
        except Exception:
            logger.warning("MCP on_lost hook raised", exc_info=True)
```

Fire site 1 — end of `_on_transport_death`, after the cancel loop:

```python
        for scope in list(self._request_scopes):
            scope.cancel()
        self._fire_lost("death")
```

Fire site 2 — `_run`'s timeout branch:

```python
        except TimeoutError as exc:
            self._fire_lost("timeout")
            raise McpTimeoutError(
                f"{operation} timed out after {self._settings.request_timeout_seconds}s"
            ) from exc
```

- [ ] **Step 3b: Extend the harness** — in `tests/mcp/conftest.py`, replace `harness_cm`'s signature and client construction (lines ~136–155):

```python
@asynccontextmanager
async def harness_cm(
    *,
    request_timeout: float = 5.0,
    on_lost: Callable[[str], None] | None = None,
) -> AsyncIterator[tuple[McpClient, Peer]]:
    """Connect an ``McpClient`` to an in-process SDK server."""
    server = build_server()
    async with create_client_server_memory_streams() as (
        (cread, cwrite),
        (sread, swrite),
    ):

        @asynccontextmanager
        async def _factory(_config: ServerConfig) -> AsyncIterator[TransportStreams]:
            # The config is ignored: we hand the client pre-built streams.
            yield TransportStreams(read=cread, write=cwrite)

        client = McpClient(
            transport_factory=_factory,
            settings=McpSettings(request_timeout_seconds=request_timeout),
            on_lost=on_lost,
        )
```

(Rest of `harness_cm` unchanged.)

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/mcp -q`
Expected: all PASS (existing tests unaffected — `on_lost` defaults to `None`)

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/client.py backend/tests/mcp/test_client.py backend/tests/mcp/conftest.py
git commit -m "feat(mcp): add on_lost hook to McpClient"
```

---

### Task 3: Manager skeleton — types, registry, status

**Files:**
- Create: `src/octave/mcp/manager.py`
- Test: `tests/mcp/test_manager.py`

- [ ] **Step 1: Write the failing tests** — create `tests/mcp/test_manager.py`:

```python
"""Lifecycle manager unit tests — fake clients, no subprocesses, no SDK.

The fake client drives the supervisor deterministically: ``die()``/``hang()``
fire the manager's on_lost hook; ``connect_error``/``ping_error`` script
outcomes. FAST settings keep every sleep in the millisecond range.
"""

from datetime import datetime

import anyio
import pytest

from octave.mcp.client import McpClient
from octave.mcp.config import McpSettings, ServerConfig, StdioConfig
from octave.mcp.errors import (
    McpConfigError,
    McpConnectionError,
    McpError,
    McpNotConnectedError,
    McpTimeoutError,
)
from octave.mcp.manager import McpServerManager, ServerStatus

FAST = McpSettings(
    request_timeout_seconds=0.05,
    restart_base_delay_seconds=0.01,
    restart_max_delay_seconds=0.05,
    restart_max_attempts=3,
    probe_timeout_seconds=0.05,
    stabilization_seconds=0.05,
)

_CONFIG = StdioConfig(command="fake-server")


class FakeClient(McpClient):
    """McpClient stand-in scripting the manager's view of a server."""

    def __init__(self, on_lost: "object | None" = None) -> None:
        super().__init__(settings=FAST, on_lost=on_lost)  # type: ignore[arg-type]
        self.connect_calls = 0
        self.aclose_calls = 0
        self.ping_calls = 0
        self.connect_error: Exception | None = None
        self.ping_error: Exception | None = None

    async def connect(self, config: ServerConfig) -> None:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error

    async def aclose(self) -> None:
        self.aclose_calls += 1

    async def ping(self) -> bool:
        self.ping_calls += 1
        if self.ping_error is not None:
            raise self.ping_error
        return True

    def die(self) -> None:
        """Simulate transport death (what the read-stream monitor does)."""
        assert self._on_lost is not None
        self._on_lost("death")

    def hang(self) -> None:
        """Simulate a request timeout (what _run's timeout branch does)."""
        assert self._on_lost is not None
        self._on_lost("timeout")


class FakeFactory:
    """Builds FakeClients and keeps them for assertions."""

    def __init__(self) -> None:
        self.clients: list[FakeClient] = []

    def __call__(self, on_lost: "object | None" = None) -> FakeClient:
        client = FakeClient(on_lost)
        self.clients.append(client)
        return client


def fail_after_first(factory: FakeFactory):  # noqa: ANN201 - test helper
    """Wrap a factory: each client's initial connect succeeds, later ones fail.

    ``connect`` is wrapped so that after the first successful connect the
    client permanently fails connects — the crash-loop scenario.
    """

    def build(on_lost):  # noqa: ANN001, ANN202 - test helper
        client = factory(on_lost)
        original_connect = client.connect

        async def connect(config: ServerConfig) -> None:
            first = client.connect_calls == 0
            await original_connect(config)
            if first:  # arm permanent failure after the initial connect
                client.connect_error = McpConnectionError("down")

        client.connect = connect  # type: ignore[method-assign]
        return client

    return build


async def wait_for(
    manager: McpServerManager,
    server_id: str,
    predicate,  # noqa: ANN001 - test helper
    timeout: float = 2.0,
) -> ServerStatus:
    """Poll status_of until ``predicate(status)`` holds (supervisor is async).

    Always predicate on observable counters (state + restart_count), never
    bare state — the supervisor can pass through a state the test would
    match before it has acted.
    """
    with anyio.fail_after(timeout):
        while True:
            status = manager.status_of(server_id)
            if predicate(status):
                return status
            await anyio.sleep(0.01)


def make_manager(*ids: str) -> tuple[McpServerManager, FakeFactory]:
    factory = FakeFactory()
    manager = McpServerManager(settings=FAST, client_factory=factory)
    for server_id in ids:
        manager.register(id=server_id, name=f"test-{server_id}", config=_CONFIG)
    return manager, factory


class TestRegistry:
    def test_register_and_status(self) -> None:
        manager, factory = make_manager("a", "b")
        assert [s.id for s in manager.status()] == ["a", "b"]
        status = manager.status_of("a")
        assert status.state == "stopped"
        assert status.name == "test-a"
        assert status.transport == "stdio"
        assert status.restart_count == 0
        assert status.consecutive_failures == 0
        assert status.last_error is None
        assert isinstance(status.last_state_change, datetime)
        assert manager.get_client("a") is factory.clients[0]

    def test_duplicate_id_raises(self) -> None:
        manager, _factory = make_manager("a")
        with pytest.raises(McpConfigError, match="duplicate"):
            manager.register(id="a", name="dupe", config=_CONFIG)

    def test_unknown_id_raises(self) -> None:
        manager, _factory = make_manager("a")
        with pytest.raises(McpConfigError, match="unknown"):
            manager.get_client("nope")
        with pytest.raises(McpConfigError, match="unknown"):
            manager.status_of("nope")

    async def test_register_after_start_all_raises(self) -> None:
        manager, _factory = make_manager("a")
        await manager.start_all()
        try:
            with pytest.raises(McpError):
                manager.register(id="b", name="late", config=_CONFIG)
        finally:
            await manager.stop_all()
```

Note: `start_all`/`stop_all` don't exist yet — `test_register_after_start_all_raises` will fail until Task 4; that's fine (it's the next task's red). Keep Tasks 3–5 uncommitted-green expectations per step; if running the whole file at Task 3 Step 4, expect exactly that one failure plus passes for the other three registry tests — implement Task 4 before committing Task 3 if the executor requires green.

- [ ] **Step 2: Run them, verify failure**

Run: `cd backend && uv run pytest tests/mcp/test_manager.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'octave.mcp.manager'`

- [ ] **Step 3: Implement the skeleton** — create `src/octave/mcp/manager.py`:

```python
"""MCP server lifecycle manager — supervision, auto-restart, health.

Role (design spec): the manager owns one ``McpClient`` per registered server
plus a supervisor task that is the **sole caller** of that client's lifecycle
methods — ``McpClient.restart()``'s single-caller contract enforced by
construction. Consumers read state via ``status()``/``status_of()`` and call
tools through ``get_client()``; restarts are transparent (same client
instance, config retained client-side).

Never imports the ``mcp`` SDK: clients are built through ``client_factory``
(see the quarantine rule in ``client.py``). ``start_all``/``stop_all`` must be
called from the same task (the lifespan task) — the task group is entered
manually so it outlives the call.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import anyio
from anyio.abc import TaskGroup

from octave.mcp.client import McpClient
from octave.mcp.config import McpSettings, ServerConfig, StdioConfig
from octave.mcp.errors import McpConfigError, McpError

__all__ = ["ClientFactory", "McpServerManager", "ServerState", "ServerStatus"]

logger = logging.getLogger(__name__)

ServerState = Literal["stopped", "starting", "connected", "restarting", "crashed"]
"""Server lifecycle states (spec state machine)."""


@dataclass(frozen=True)
class ServerStatus:
    """Point-in-time snapshot of one managed server."""

    id: str
    name: str
    state: ServerState
    transport: str
    """``"stdio" | "http"`` — display metadata derived from the config type."""

    restart_count: int
    """Successful auto-restart cycles since registration."""

    consecutive_failures: int
    """Current backoff streak; reset by stabilization or manual restart."""

    last_error: str | None
    """Most recent failure reason (Octave exception message — already redacted)."""

    last_state_change: datetime


ClientFactory = Callable[[Callable[[str], None]], McpClient]
"""Builds a client wired to the manager's ``on_lost(reason)`` hook."""


@dataclass
class _ManagedServer:
    """Internal bookkeeping for one registered server."""

    id: str
    name: str
    config: ServerConfig
    client: McpClient
    state: ServerState = "stopped"
    restart_count: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    last_state_change: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    wake: anyio.Event = field(default_factory=anyio.Event)
    reason: str | None = None
    """Latest on_lost signal: ``"death"`` wins over ``"timeout"``."""

    cmd_restart: bool = False
    scope: anyio.CancelScope | None = None
    stable_deadline: float = 0.0
    """anyio clock time after which the failure counter may reset."""


def _default_client_factory(settings: McpSettings) -> ClientFactory:
    def build(on_lost: Callable[[str], None]) -> McpClient:
        return McpClient(settings=settings, on_lost=on_lost)

    return build


class McpServerManager:
    """Registry + supervisor for a fleet of MCP server connections."""

    def __init__(
        self,
        *,
        settings: McpSettings | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._settings = settings or McpSettings()
        self._client_factory = client_factory or _default_client_factory(
            self._settings
        )
        self._servers: dict[str, _ManagedServer] = {}
        self._task_group: TaskGroup | None = None

    def register(self, *, id: str, name: str, config: ServerConfig) -> None:
        """Add a server. Allowed only before ``start_all()``.

        Raises ``McpConfigError`` on duplicate id, ``McpError`` after start.
        """
        if self._task_group is not None:
            raise McpError("cannot register after start_all()")
        if id in self._servers:
            raise McpConfigError(f"duplicate server id: {id}")
        self._servers[id] = _ManagedServer(
            id=id,
            name=name,
            config=config,
            client=self._client_factory(
                lambda reason, _id=id: self._on_lost(_id, reason)
            ),
        )

    def _on_lost(self, id: str, reason: str) -> None:
        """Client hook: record the signal, wake the supervisor.

        Synchronous; safe to call from any task (SDK receive loop or caller).
        """
        rec = self._servers[id]
        if reason == "death" or rec.reason is None:
            rec.reason = reason
        rec.wake.set()

    def get_client(self, id: str) -> McpClient:
        """The client for ``id`` — same instance across restarts."""
        return self._require(id).client

    def status(self) -> list[ServerStatus]:
        """Snapshot of every registered server, registration order."""
        return [self._status_of(rec) for rec in self._servers.values()]

    def status_of(self, id: str) -> ServerStatus:
        """Snapshot of one server."""
        return self._status_of(self._require(id))

    def _require(self, id: str) -> _ManagedServer:
        rec = self._servers.get(id)
        if rec is None:
            raise McpConfigError(f"unknown server id: {id}")
        return rec

    def _status_of(self, rec: _ManagedServer) -> ServerStatus:
        return ServerStatus(
            id=rec.id,
            name=rec.name,
            state=rec.state,
            transport="stdio" if isinstance(rec.config, StdioConfig) else "http",
            restart_count=rec.restart_count,
            consecutive_failures=rec.consecutive_failures,
            last_error=rec.last_error,
            last_state_change=rec.last_state_change,
        )

    def _set_state(self, rec: _ManagedServer, state: ServerState) -> None:
        rec.state = state
        rec.last_state_change = datetime.now(timezone.utc)
        logger.info("MCP server state | id=%s state=%s", rec.id, state)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/mcp/test_manager.py::TestRegistry -q -k "not after_start_all"`
Expected: 3 PASS (`test_register_after_start_all_raises` is Task 4's red — skip or expect it failing)

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/manager.py backend/tests/mcp/test_manager.py
git commit -m "feat(mcp): manager skeleton — registry, status, on_lost routing"
```

---

### Task 4: Supervisor loop — start/stop, auto-restart, backoff, crash-loop

**Files:**
- Modify: `src/octave/mcp/manager.py` (add `start_all`, `start`, `stop`, `stop_all`, `restart`, `_supervise`, `_run_server_loop`, `_attempt_connect`, `_probe_ok`, `_mark_connected`; add `McpNotConnectedError` to the errors import)
- Test: `tests/mcp/test_manager.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/mcp/test_manager.py`:

```python
class TestSupervision:
    async def test_start_all_connects_and_stop_all_closes(self) -> None:
        manager, factory = make_manager("a", "b")
        await manager.start_all()
        try:
            await wait_for(manager, "a", lambda s: s.state == "connected")
            await wait_for(manager, "b", lambda s: s.state == "connected")
        finally:
            await manager.stop_all()
        assert manager.status_of("a").state == "stopped"
        assert manager.status_of("b").state == "stopped"
        assert all(c.aclose_calls >= 1 for c in factory.clients)

    async def test_death_triggers_auto_restart(self) -> None:
        manager, factory = make_manager("a")
        await manager.start_all()
        try:
            await wait_for(manager, "a", lambda s: s.state == "connected")
            factory.clients[0].die()
            # Predicate on restart_count, not bare state: the supervisor
            # passes through connected→restarting→connected and a bare
            # "connected" wait could return before it even wakes.
            status = await wait_for(
                manager, "a", lambda s: s.state == "connected" and s.restart_count == 1
            )
            assert status.consecutive_failures == 1  # not reset yet
        finally:
            await manager.stop_all()

    async def test_connect_failure_backs_off_then_crashes(self) -> None:
        manager, factory = make_manager("a")
        factory.clients[0].connect_error = McpConnectionError("boom")
        await manager.start_all()
        try:
            status = await wait_for(manager, "a", lambda s: s.state == "crashed")
            assert status.consecutive_failures == FAST.restart_max_attempts
            assert "boom" in (status.last_error or "")
            # initial attempt + restart_max_attempts retries, no more:
            assert factory.clients[0].connect_calls == 1 + FAST.restart_max_attempts
        finally:
            await manager.stop_all()

    async def test_stop_cancels_supervisor_mid_backoff(self) -> None:
        factory = FakeFactory()
        manager = McpServerManager(settings=FAST, client_factory=fail_after_first(factory))
        manager.register(id="a", name="a", config=_CONFIG)
        await manager.start_all()
        await wait_for(manager, "a", lambda s: s.state == "connected")
        factory.clients[0].die()  # supervisor enters backoff; retries all fail
        # FAST backoff totals ~70ms before crash — accept either mid-cycle
        # state; the point is stop() works while the supervisor is busy.
        await wait_for(manager, "a", lambda s: s.state in ("restarting", "crashed"))
        await manager.stop("a")
        status = await wait_for(manager, "a", lambda s: s.state == "stopped")
        assert status.state == "stopped"
        assert factory.clients[0].aclose_calls >= 1  # shielded finally ran

    async def test_manual_restart_on_stopped_raises(self) -> None:
        manager, _factory = make_manager("a")
        with pytest.raises(McpNotConnectedError):
            await manager.restart("a")

    async def test_manual_restart_exits_crashed(self) -> None:
        factory = FakeFactory()
        manager = McpServerManager(settings=FAST, client_factory=fail_after_first(factory))
        manager.register(id="a", name="a", config=_CONFIG)
        await manager.start_all()
        factory.clients[0].die()
        status = await wait_for(manager, "a", lambda s: s.state == "crashed")
        assert status.consecutive_failures == FAST.restart_max_attempts
        # Recovery: clear the scripted failure, manual restart cycles.
        factory.clients[0].connect_error = None
        await manager.restart("a")
        status = await wait_for(
            manager, "a", lambda s: s.state == "connected" and s.consecutive_failures == 0
        )
        assert status.restart_count >= 1
        await manager.stop_all()

    async def test_supervisor_exception_contains_to_crashed(self) -> None:
        factory = FakeFactory()
        manager = McpServerManager(settings=FAST, client_factory=factory)
        manager.register(id="a", name="a", config=_CONFIG)
        factory.clients[0].connect_error = RuntimeError("manager bug")
        await manager.start_all()
        try:
            status = await wait_for(manager, "a", lambda s: s.state == "crashed")
            assert status.state == "crashed"
        finally:
            await manager.stop_all()

    async def test_multi_server_independence(self) -> None:
        manager, factory = make_manager("bad", "good")
        factory.clients[0].connect_error = McpConnectionError("bad binary")
        await manager.start_all()
        try:
            await wait_for(manager, "bad", lambda s: s.state == "crashed")
            await wait_for(manager, "good", lambda s: s.state == "connected")
            factory.clients[1].die()  # 'good' recovers despite 'bad' crashed
            await wait_for(
                manager, "good", lambda s: s.state == "connected" and s.restart_count == 1
            )
        finally:
            await manager.stop_all()
```

- [ ] **Step 2: Run them, verify failure**

Run: `cd backend && uv run pytest tests/mcp/test_manager.py::TestSupervision -q`
Expected: FAIL — `AttributeError: 'McpServerManager' object has no attribute 'start_all'`

- [ ] **Step 3: Implement** — add to `src/octave/mcp/manager.py` (inside `McpServerManager`; extend the errors import to `from octave.mcp.errors import McpConfigError, McpError, McpNotConnectedError`):

```python
    async def start_all(self) -> None:
        """Enter the task group and spawn one supervisor per server.

        Returns once supervisors are spawned — not once servers are healthy;
        observe readiness via ``status()``. Must be called from the same task
        as ``stop_all()`` (the task group is entered manually).
        """
        if self._task_group is not None:
            raise McpError("start_all() already called")
        self._task_group = anyio.create_task_group()
        await self._task_group.__aenter__()
        for rec in self._servers.values():
            if rec.state == "stopped":
                await self.start(rec.id)

    async def stop_all(self) -> None:
        """Cancel every supervisor and await teardown."""
        if self._task_group is None:
            return
        for rec in self._servers.values():
            if rec.scope is not None:
                rec.scope.cancel()
        task_group, self._task_group = self._task_group, None
        await task_group.__aexit__(None, None, None)

    async def start(self, id: str) -> None:
        """Spawn a stopped server's supervisor."""
        if self._task_group is None:
            raise McpError("manager not started — call start_all() first")
        rec = self._require(id)
        if rec.state != "stopped":
            raise McpError(f"server {id} already started (state={rec.state})")
        rec.scope = anyio.CancelScope()
        self._task_group.start_soon(self._supervise, rec)

    async def stop(self, id: str) -> None:
        """Cancel a supervisor; its ``finally`` block closes the client.

        Cancellation *is* the stop mechanism, so stop/restart cannot race.
        """
        rec = self._require(id)
        if rec.scope is not None:
            rec.scope.cancel()

    async def restart(self, id: str) -> None:
        """Supervisor-driven restart cycle; resets the failure counter.

        The only exit from ``crashed``. From ``stopped`` →
        ``McpNotConnectedError``.
        """
        rec = self._require(id)
        if rec.state == "stopped":
            raise McpNotConnectedError(f"cannot restart {id}: not started")
        rec.cmd_restart = True
        rec.wake.set()

    async def _supervise(self, rec: _ManagedServer) -> None:
        """Task body wrapper: the shielded teardown is the stop mechanism."""
        try:
            await self._run_server_loop(rec)
        finally:
            with anyio.CancelScope(shield=True):
                await rec.client.aclose()
            self._set_state(rec, "stopped")

    async def _run_server_loop(self, rec: _ManagedServer) -> None:
        """Supervision loop — the sole caller of rec.client lifecycle methods.

        Phases (spec state machine): initial connect → park on ``wake`` →
        restart cycles with exponential backoff → ``crashed`` parks until
        manual ``restart()``. Any unexpected exception is contained: logged,
        ``crashed``, parked — one server's bug never kills another's
        supervision and never loops silently.
        """
        settings = self._settings
        started = False
        with rec.scope:
            while True:
                try:
                    if not started:
                        started = True
                        self._set_state(rec, "starting")
                        if await self._attempt_connect(rec):
                            self._mark_connected(rec, count_restart=False)
                        else:
                            self._set_state(rec, "restarting")
                    elif rec.state in ("connected", "crashed"):
                        await rec.wake.wait()
                        rec.wake = anyio.Event()
                        reason, cmd = rec.reason, rec.cmd_restart
                        rec.reason, rec.cmd_restart = None, False
                        if rec.state == "connected":
                            if anyio.current_time() >= rec.stable_deadline:
                                rec.consecutive_failures = 0
                            if reason == "timeout" and not cmd:
                                if await self._probe_ok(rec):
                                    continue  # healthy-but-slow: keep serving
                        if cmd:
                            rec.consecutive_failures = 0
                        self._set_state(rec, "restarting")
                    await rec.client.aclose()
                    while rec.consecutive_failures < settings.restart_max_attempts:
                        rec.consecutive_failures += 1
                        delay = min(
                            settings.restart_base_delay_seconds
                            * 2 ** (rec.consecutive_failures - 1),
                            settings.restart_max_delay_seconds,
                        )
                        logger.warning(
                            "restarting MCP server | id=%s attempt=%s delay=%.2fs",
                            rec.id,
                            rec.consecutive_failures,
                            delay,
                        )
                        await anyio.sleep(delay)
                        if await self._attempt_connect(rec):
                            self._mark_connected(rec, count_restart=True)
                            break
                    else:
                        logger.error(
                            "MCP server crashed | id=%s attempts=%s last_error=%s",
                            rec.id,
                            settings.restart_max_attempts,
                            rec.last_error,
                        )
                        self._set_state(rec, "crashed")
                except Exception:
                    logger.exception(
                        "MCP supervisor failure contained | id=%s", rec.id
                    )
                    self._set_state(rec, "crashed")

    async def _attempt_connect(self, rec: _ManagedServer) -> bool:
        """One connect try; McpError failures recorded, never raised to the loop."""
        try:
            await rec.client.connect(rec.config)
        except McpError as exc:
            rec.last_error = str(exc)
            logger.warning("MCP connect failed | id=%s error=%s", rec.id, exc)
            return False
        return True

    async def _probe_ok(self, rec: _ManagedServer) -> bool:
        """Confirming ping within ``probe_timeout_seconds`` (spec: probe
        budget). True only if the server answered in budget."""
        try:
            with anyio.fail_after(self._settings.probe_timeout_seconds):
                return await rec.client.ping()
        except (TimeoutError, McpError):
            return False

    def _mark_connected(self, rec: _ManagedServer, *, count_restart: bool) -> None:
        if count_restart:
            rec.restart_count += 1
        rec.stable_deadline = (
            anyio.current_time() + self._settings.stabilization_seconds
        )
        self._set_state(rec, "connected")
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/mcp/test_manager.py -q`
Expected: all PASS (including `test_register_after_start_all_raises` from Task 3)

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/manager.py backend/tests/mcp/test_manager.py
git commit -m "feat(mcp): supervisor loop — auto-restart, backoff, crash-loop, stop-via-cancel"
```

---

### Task 5: Probe-on-timeout + stabilization behavior lock

**Files:**
- Modify: `src/octave/mcp/manager.py` only if a test exposes a gap (the loop already implements the probe branch — this task locks behavior with tests)
- Test: `tests/mcp/test_manager.py`

- [ ] **Step 1: Write the tests** — append to `tests/mcp/test_manager.py`:

```python
class TestProbeAndStabilization:
    async def test_timeout_probe_ok_keeps_server_connected(self) -> None:
        manager, factory = make_manager("a")
        await manager.start_all()
        try:
            await wait_for(manager, "a", lambda s: s.state == "connected")
            factory.clients[0].hang()  # request timed out; server answers ping
            await anyio.sleep(0.1)  # give the supervisor time to act
            status = manager.status_of("a")
            assert status.state == "connected"
            assert status.restart_count == 0
            assert factory.clients[0].ping_calls == 1
            assert factory.clients[0].aclose_calls == 0
        finally:
            await manager.stop_all()

    async def test_timeout_probe_failure_restarts(self) -> None:
        manager, factory = make_manager("a")
        await manager.start_all()
        try:
            await wait_for(manager, "a", lambda s: s.state == "connected")
            factory.clients[0].ping_error = McpTimeoutError("ping wedged")
            factory.clients[0].hang()
            status = await wait_for(
                manager, "a", lambda s: s.state == "connected" and s.restart_count == 1
            )
            assert status.restart_count == 1
        finally:
            await manager.stop_all()

    async def test_stabilization_resets_failure_counter(self) -> None:
        manager, factory = make_manager("a")
        await manager.start_all()
        try:
            await wait_for(
                manager, "a", lambda s: s.state == "connected" and s.restart_count == 1
            )
            assert manager.status_of("a").consecutive_failures == 1
            factory.clients[0].die()  # re-die inside the stabilization window
            await wait_for(
                manager, "a", lambda s: s.state == "connected" and s.restart_count == 2
            )
            assert manager.status_of("a").consecutive_failures == 2  # escalated
            await anyio.sleep(FAST.stabilization_seconds + 0.05)
            factory.clients[0].die()  # died after holding past the window
            status = await wait_for(
                manager, "a", lambda s: s.state == "connected" and s.restart_count == 3
            )
            assert status.consecutive_failures == 1  # counter reset
        finally:
            await manager.stop_all()
```

- [ ] **Step 2: Run them**

Run: `cd backend && uv run pytest tests/mcp/test_manager.py::TestProbeAndStabilization -q`
Expected: PASS if Task 4 landed the full loop; if any FAIL, fix the loop (do not weaken the test).

- [ ] **Step 3: Run the full MCP suite**

Run: `cd backend && uv run pytest tests/mcp -q`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add backend/tests/mcp/test_manager.py backend/src/octave/mcp/manager.py
git commit -m "test(mcp): lock probe-on-timeout and stabilization counter reset"
```

---

### Task 6: Deps seam + package exports

**Files:**
- Modify: `src/octave/mcp/deps.py` (full rewrite)
- Modify: `src/octave/mcp/__init__.py`
- Test: `tests/test_mcp_deps.py` (full rewrite)

- [ ] **Step 1: Write the failing tests** — replace `tests/test_mcp_deps.py` entirely:

```python
"""get_mcp_manager resolver: app.state lookup, overrides, 503 when unset.

get_mcp_client was removed in #18: with N managed servers it cannot resolve
"the" client — consumers pick a server via manager.get_client(id).
"""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from octave.mcp.deps import get_mcp_manager
from octave.mcp.manager import McpServerManager


def _probe_app() -> FastAPI:
    """A minimal app whose only route depends on the manager."""
    app = FastAPI()

    @app.get("/probe")
    async def _probe(
        manager: McpServerManager = Depends(get_mcp_manager),
    ) -> dict[str, str]:
        return {"manager": type(manager).__name__}

    return app


def test_unset_state_returns_503() -> None:
    response = TestClient(_probe_app()).get("/probe")
    assert response.status_code == 503
    assert response.json()["detail"] == "MCP manager not configured"


def test_state_provides_manager() -> None:
    app = _probe_app()
    app.state.mcp_manager = McpServerManager()
    response = TestClient(app).get("/probe")
    assert response.status_code == 200
    assert response.json() == {"manager": "McpServerManager"}


def test_dependency_override_wins() -> None:
    class FakeManager(McpServerManager):
        """No-op stand-in proving routes resolve through the override seam."""

    app = _probe_app()
    app.dependency_overrides[get_mcp_manager] = lambda: FakeManager()
    response = TestClient(app).get("/probe")
    assert response.status_code == 200
    assert response.json() == {"manager": "FakeManager"}


def test_get_mcp_client_is_gone() -> None:
    import octave.mcp
    import octave.mcp.deps

    assert not hasattr(octave.mcp, "get_mcp_client")
    assert not hasattr(octave.mcp.deps, "get_mcp_client")
```

- [ ] **Step 2: Run it, verify failure**

Run: `cd backend && uv run pytest tests/test_mcp_deps.py -q`
Expected: FAIL — `ImportError: cannot import name 'get_mcp_manager'`

- [ ] **Step 3: Implement** — replace `src/octave/mcp/deps.py`:

```python
"""FastAPI dependency resolver for the MCP server manager.

Thin seam (spec decision 4): ``mcp_lifespan`` publishes
``app.state.mcp_manager``; routes resolve through it and pick servers with
``manager.get_client(id)``. ``get_mcp_client`` was removed in #18 — with N
servers it cannot resolve "the" client.
"""

from fastapi import HTTPException, Request

from octave.mcp.manager import McpServerManager

__all__ = ["get_mcp_manager"]


async def get_mcp_manager(request: Request) -> McpServerManager:
    """Resolve the app-wide manager from ``app.state.mcp_manager``.

    Raises 503 while no manager is configured — Octave boots fine without
    any MCP servers.
    """
    manager: McpServerManager | None = getattr(
        request.app.state, "mcp_manager", None
    )
    if manager is None:
        raise HTTPException(status_code=503, detail="MCP manager not configured")
    return manager
```

Update `src/octave/mcp/__init__.py`: remove `from octave.mcp.deps import get_mcp_client` and `"get_mcp_client"` from `__all__`; add (keep `__all__` alphabetical):

```python
from octave.mcp.deps import get_mcp_manager
from octave.mcp.manager import McpServerManager, ServerState, ServerStatus
```

with `"McpServerManager"`, `"ServerState"`, `"ServerStatus"`, `"get_mcp_manager"` added to `__all__`. **Note:** the `mcp_lifespan` import comes in Task 7 (the module doesn't exist yet).

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/test_mcp_deps.py tests/mcp -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/deps.py backend/src/octave/mcp/__init__.py backend/tests/test_mcp_deps.py
git commit -m "refactor(mcp): resolve deps through the manager; drop single-client seam"
```

---

### Task 7: `mcp_lifespan` + app wiring

**Files:**
- Create: `src/octave/mcp/lifespan.py`
- Modify: `src/octave/app.py`
- Modify: `src/octave/mcp/__init__.py` (add `mcp_lifespan` export)
- Test: `tests/mcp/test_lifespan.py`

- [ ] **Step 1: Write the failing tests** — create `tests/mcp/test_lifespan.py`:

```python
"""mcp_lifespan + compose_lifespans: startup, shutdown, ordering."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from octave.app import compose_lifespans
from octave.mcp.config import StdioConfig
from octave.mcp.lifespan import mcp_lifespan
from octave.mcp.manager import McpServerManager
from tests.mcp.test_manager import FakeFactory


def _app_with(factory: FakeFactory) -> FastAPI:
    configs = (("s1", "Server One", StdioConfig(command="fake")),)

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with mcp_lifespan(app, configs=configs, client_factory=factory):
            yield

    return FastAPI(lifespan=_lifespan)


def test_startup_connects_and_shutdown_closes() -> None:
    factory = FakeFactory()
    app = _app_with(factory)
    with TestClient(app) as client:
        manager: McpServerManager = client.app.state.mcp_manager
        status = manager.status_of("s1")
        assert status.state == "connected"
        assert status.name == "Server One"
    assert factory.clients[0].aclose_calls >= 1
    assert manager.status_of("s1").state == "stopped"


def test_default_configs_boots_empty() -> None:
    app = FastAPI(lifespan=mcp_lifespan)
    with TestClient(app) as client:
        assert client.app.state.mcp_manager.status() == []


def test_compose_lifespans_orders_start_and_stop() -> None:
    events: list[str] = []

    def make_lifespan(tag: str):  # noqa: ANN202 - test helper
        @asynccontextmanager
        async def _lf(_app: FastAPI) -> AsyncIterator[None]:
            events.append(f"start-{tag}")
            yield
            events.append(f"stop-{tag}")

        return _lf

    app = FastAPI(
        lifespan=compose_lifespans(make_lifespan("a"), make_lifespan("b"))
    )
    with TestClient(app):
        assert events == ["start-a", "start-b"]
    assert events == ["start-a", "start-b", "stop-b", "stop-a"]
```

- [ ] **Step 2: Run them, verify failure**

Run: `cd backend && uv run pytest tests/mcp/test_lifespan.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'octave.mcp.lifespan'`

- [ ] **Step 3: Implement** — create `src/octave/mcp/lifespan.py`:

```python
"""App lifespan for the MCP fleet: build → register → start → publish.

Startup posture (spec decision 6): a server that can't start does NOT abort
app boot — its supervisor enters the backoff/crashed path with ERROR logs.
Deliberate divergence from db_lifespan's fail-fast: MCP servers are
peripheral; the schema is not.

``start_all``/``stop_all`` run in the lifespan task, satisfying the manager's
same-task requirement for manual task-group entry.
"""

import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from octave.mcp.config import ServerConfig
from octave.mcp.manager import ClientFactory, McpServerManager

__all__ = ["mcp_lifespan"]

logger = logging.getLogger(__name__)


@asynccontextmanager
async def mcp_lifespan(
    app: FastAPI,
    *,
    configs: Iterable[tuple[str, str, ServerConfig]] = (),
    client_factory: ClientFactory | None = None,
) -> AsyncIterator[None]:
    """Manage the MCP fleet for the app's lifetime.

    ``configs`` yields ``(id, name, config)`` triples. The default empty
    source keeps Octave booting with zero MCP servers until config
    persistence (#7) replaces it with DB rows.
    """
    entries = list(configs)
    manager = McpServerManager(client_factory=client_factory)
    for server_id, name, config in entries:
        manager.register(id=server_id, name=name, config=config)
    await manager.start_all()
    app.state.mcp_manager = manager
    logger.info("MCP manager ready: %s server(s)", len(entries))
    try:
        yield
    finally:
        await manager.stop_all()
        logger.info("MCP manager stopped")
```

Replace `src/octave/app.py` entirely:

```python
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI

from octave.db.lifespan import db_lifespan
from octave.mcp.lifespan import mcp_lifespan
from octave.middleware import LogRequestMiddleware, add_cors, add_error_handlers
from octave.routes.health import router as health_router
from octave.websocket.connection import router as ws_router

AppLifespan = Callable[[FastAPI], AsyncIterator[None]]
"""A lifespan callable: async context factory taking the app."""


def compose_lifespans(*lifespans: AppLifespan) -> AppLifespan:
    """Compose lifespans: enter in order, exit in reverse (stack semantics)."""

    @asynccontextmanager
    async def _composed(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            for lifespan in lifespans:
                await stack.enter_async_context(lifespan(app))
            yield

    return _composed


app = FastAPI(
    title="Octave Backend", lifespan=compose_lifespans(db_lifespan, mcp_lifespan)
)

# Middleware
add_cors(app)
add_error_handlers(app)
app.add_middleware(LogRequestMiddleware)

# Routes
app.include_router(health_router, prefix="/api")
app.include_router(ws_router)
```

Add to `src/octave/mcp/__init__.py` (deferred from Task 6): `from octave.mcp.lifespan import mcp_lifespan` and `"mcp_lifespan"` in `__all__`.

- [ ] **Step 4: Run tests, verify pass**

Run: `cd backend && uv run pytest tests/mcp/test_lifespan.py tests/test_mcp_deps.py tests/test_health.py -q`
Expected: all PASS (`test_health.py` proves the composed db+mcp lifespan boots the real app with empty configs)

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/lifespan.py backend/src/octave/app.py backend/src/octave/mcp/__init__.py backend/tests/mcp/test_lifespan.py
git commit -m "feat(mcp): mcp_lifespan wires the manager into the app"
```

---

### Task 8: Real-subprocess integration

**Files:**
- Test: `tests/mcp/test_stdio_integration.py` (append)

- [ ] **Step 1: Write the tests** — append to `tests/mcp/test_stdio_integration.py` (the file already skips via `OCTAVE_MCP_SKIP_SUBPROCESS_TESTS=1`):

```python
async def test_manager_auto_restarts_real_subprocess() -> None:
    """Death mid-call → supervisor respawns → tool round-trips again."""
    from octave.mcp.config import McpSettings as _Settings
    from octave.mcp.manager import McpServerManager
    from tests.mcp.test_manager import wait_for

    settings = _Settings(
        request_timeout_seconds=10.0,
        restart_base_delay_seconds=0.1,
        restart_max_attempts=3,
    )
    manager = McpServerManager(settings=settings)
    manager.register(
        id="dying",
        name="Dying",
        config=StdioConfig(command=sys.executable, args=[str(_DYING_SERVER)]),
    )
    await manager.start_all()
    try:
        await wait_for(
            manager, "dying", lambda s: s.state == "connected", timeout=15
        )
        client = manager.get_client("dying")
        with pytest.raises(McpConnectionError):
            await client.call_tool("exit_now")
        # Supervisor notices death and auto-restarts (predicate on
        # restart_count so we don't observe the pre-reaction connected).
        await wait_for(
            manager,
            "dying",
            lambda s: s.state == "connected" and s.restart_count >= 1,
            timeout=15,
        )
        result = await client.call_tool("echo", {"text": "auto-reborn"})
        assert result.content[0].text == "auto-reborn"
    finally:
        await manager.stop_all()


async def test_manager_crashes_on_unstartable_server_without_blocking_app() -> None:
    """A bad binary exhausts backoff into 'crashed'; stop_all stays clean."""
    from octave.mcp.config import McpSettings as _Settings
    from octave.mcp.manager import McpServerManager
    from tests.mcp.test_manager import wait_for

    settings = _Settings(
        restart_base_delay_seconds=0.01,
        restart_max_delay_seconds=0.05,
        restart_max_attempts=2,
    )
    manager = McpServerManager(settings=settings)
    manager.register(
        id="bad",
        name="Bad",
        config=StdioConfig(command="/nonexistent/octave-test-binary"),
    )
    await manager.start_all()
    try:
        await wait_for(manager, "bad", lambda s: s.state == "crashed", timeout=15)
        assert manager.status_of("bad").consecutive_failures == 2
    finally:
        await manager.stop_all()
    assert manager.status_of("bad").state == "stopped"
```

- [ ] **Step 2: Run them**

Run: `cd backend && uv run pytest tests/mcp/test_stdio_integration.py -q`
Expected: PASS (or SKIP if `OCTAVE_MCP_SKIP_SUBPROCESS_TESTS=1`)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/mcp/test_stdio_integration.py
git commit -m "test(mcp): manager auto-restart and crash-loop against real subprocesses"
```

---

### Task 9: Docs, lint, type-check, full suite

**Files:**
- Modify: `docs/ARCHITECTURE.md` (MCP Connector section)
- Modify: `docs/TODO.md` (line 78)

- [ ] **Step 1: Update `docs/TODO.md`** — replace line 78:

```markdown
- [ ] 4. Build server lifecycle manager (start, stop, restart, health monitoring)
```

with:

```markdown
- [x] 4. Build server lifecycle manager (start, stop, restart, health monitoring) — PR #93 (per-server supervisors, backoff + crash-loop, probe-on-timeout; REST surface deferred to MCP Connector UI)
```

- [ ] **Step 2: Update `docs/ARCHITECTURE.md`** — read the MCP Connector section; append to the "Server lifecycle manager (start/stop/restart/health)" bullet: `(shipped: octave.mcp.manager — supervisors, auto-restart with backoff + crash-loop detection, probe-on-timeout health, PR #93)`. Confirm "Configuration persistence" still reads as pending on #7.

- [ ] **Step 3: Full verification**

```bash
cd backend && uv run pytest -q
uv run ruff check src tests
uv run mypy src
```

Expected: pytest all PASS; ruff `All checks passed!`; mypy `Success: no issues found in N source files`. Fix any findings — they are plan bugs, not noise.

- [ ] **Step 4: Commit**

```bash
git add docs/ARCHITECTURE.md docs/TODO.md
git commit -m "docs(mcp): lifecycle manager shipped (MCP Connector #4)"
```

---

## Spec coverage map (self-review)

| Spec requirement | Task |
|---|---|
| 5 supervision settings, defaults | 1 |
| `on_lost` hook: two fire sites, exception suppression, default None | 2 |
| Registry: duplicate/unknown id errors, register-after-start error | 3 |
| `status`/`status_of`/`get_client` (same instance) | 3 |
| State machine: starting/connected/restarting/crashed/stopped | 4 |
| Backoff `min(base·2^(n−1), cap)`, crash-loop, bounded attempts | 4 |
| Stop via cancellation, shielded aclose | 4 |
| Manual restart exits crashed; stopped → `McpNotConnectedError` | 4 |
| Supervisor exception containment | 4 |
| Multi-server independence | 4 |
| Probe-on-timeout: ok → keep serving; fail → restart | 5 |
| Stabilization counter reset | 5 |
| `get_mcp_manager` replaces `get_mcp_client` | 6 |
| `mcp_lifespan` + `compose_lifespans` + app wiring + empty default | 7 |
| Real-subprocess auto-restart + crash-loop | 8 |
| Docs + lint/type gates | 9 |
| Not in this plan (spec deferrals): REST endpoints, DB configs (#7), WebSocket state push | — |
