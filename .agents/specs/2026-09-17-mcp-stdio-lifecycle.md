# MCP stdio Subprocess Lifecycle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two issue-#16 gaps in the MCP client — detect subprocess exit mid-session (fail fast, `is_connected`) and add manual `restart()` — with all changes inside `octave.mcp.client`.

**Architecture:** The client wraps the transport's read stream in a `_MonitoredReadStream` proxy; unexpected stream end (EOF / closed-resource / `Exception` item — what SDK `stdio_client` emits when the subprocess dies) marks the client disconnected and cancels in-flight requests via client-owned cancel scopes. `connect()` retains its `ServerConfig` so `restart()` = `aclose()` + `connect(config)`. `transport.py` and the quarantine rule are untouched. Design: [`.agents/specs/2026-09-17-mcp-stdio-lifecycle-design.md`](./2026-09-17-mcp-stdio-lifecycle-design.md).

**Tech Stack:** Python 3.12, `mcp>=1.30,<2.0` SDK (quarantined), anyio, pytest (asyncio auto-mode), `uv`.

**Issue:** #16 · **Branch:** `feature/mcp-stdio-transport` · **Draft PR:** [#91](https://github.com/Svagtlys/Octave/pull/91)

**Conventions for every task:**

- All commands run from `backend/` (`cd backend` first). Python via `uv run …`.
- TDD: write the failing test, run it (verify it fails for the expected reason), implement, verify pass, commit.
- Commit messages: `type(scope): description`.
- Do not modify `transport.py`, `config.py`, `errors.py`, `types.py`, `deps.py`, or `tests/mcp/conftest.py`.

**File map:**

| File | Action | Responsibility |
|---|---|---|
| `src/octave/mcp/client.py` | Modify (all tasks) | monitor proxy, death handler, cancel-scope registry, `is_connected`, `restart()`, error translation, logging |
| `src/octave/mcp/errors.py` | Modify (Task 2) | `McpConnectionError` docstring only |
| `tests/mcp/test_client.py` | Modify (Tasks 1–4) | stub-peer harness + death/restart unit tests |
| `tests/mcp/fixtures/dying_server.py` | Create (Task 5) | self-terminating stdio MCP server |
| `tests/mcp/test_stdio_integration.py` | Modify (Task 5) | exit → restart integration test |
| `docs/ARCHITECTURE.md`, `docs/TODO.md` | Modify (Task 6) | status updates |

---

### Task 1: Death detection + `is_connected`

**Files:**
- Modify: `src/octave/mcp/client.py`
- Test: `tests/mcp/test_client.py` (append)

- [ ] **Step 1: Commit the design doc and this plan**

```bash
cd "$(git rev-parse --show-toplevel)"
git add .agents/specs/2026-09-17-mcp-stdio-lifecycle-design.md .agents/specs/2026-09-17-mcp-stdio-lifecycle.md
git commit -m "docs(specs): add MCP stdio lifecycle design and plan"
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/mcp/test_client.py`. First extend the imports at the top of the file — add `logging`, `TracebackType`, and `asynccontextmanager` to the typing/contextlib imports so the header block reads (keep every existing import line, add what's missing):

```python
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any
```

and extend the `mcp`/anyio imports that already exist: `MemoryObjectReceiveStream`, `MemoryObjectSendStream` from `anyio.streams.memory` (add to the existing import block):

```python
from anyio.streams.memory import (
    MemoryObjectReceiveStream,
    MemoryObjectSendStream,
)
```

Then append the shared stub-peer helpers and the three failing tests at the end of the file:

```python
# ---------------------------------------------------------------------------
# Stub-peer harness for exit-detection tests. The peer answers ONLY the
# initialize handshake; everything else (pings, tool calls) goes unanswered,
# and tests kill the connection by closing the peer's send stream or pushing
# an Exception item — exactly what SDK stdio_client emits on subprocess
# death. Mirrors the standalone stub in test_inbound_request_rejected…
# ---------------------------------------------------------------------------


async def _answer_initialize_only(
    sread: MemoryObjectReceiveStream[SessionMessage | Exception],
    swrite: MemoryObjectSendStream[SessionMessage],
) -> None:
    """Minimal peer: respond to initialize, ignore everything else."""
    async for message in sread:
        root = message.message.root
        if isinstance(root, JSONRPCRequest) and root.method == "initialize":
            await swrite.send(
                SessionMessage(
                    JSONRPCMessage(
                        root=JSONRPCResponse(
                            jsonrpc="2.0", id=root.id, result=_INIT_RESULT
                        )
                    )
                )
            )


@asynccontextmanager
async def _stub_harness(
    *, request_timeout: float = 30.0
) -> AsyncIterator[
    tuple[McpClient, MemoryObjectSendStream[SessionMessage | Exception]]
]:
    """Client connected to the stub peer. Yields (client, peer_send_stream)."""
    async with create_client_server_memory_streams() as (
        (cread, cwrite),
        (sread, swrite),
    ):

        @asynccontextmanager
        async def _factory(_config: ServerConfig) -> AsyncIterator[TransportStreams]:
            yield TransportStreams(read=cread, write=cwrite)

        client = McpClient(
            transport_factory=_factory,
            settings=McpSettings(request_timeout_seconds=request_timeout),
        )
        async with anyio.create_task_group() as tg:
            tg.start_soon(_answer_initialize_only, sread, swrite)
            await client.connect(StdioConfig(command="stub-peer"))
            try:
                yield client, swrite
            finally:
                await client.aclose()
                tg.cancel_scope.cancel()


async def test_is_connected_tracks_lifecycle() -> None:
    assert McpClient().is_connected is False
    async with _stub_harness() as (client, _swrite):
        assert client.is_connected is True
    assert client.is_connected is False


async def test_stream_close_marks_disconnected() -> None:
    async with _stub_harness() as (client, swrite):
        # EOF — what a dead subprocess pipe looks like to the reader.
        await swrite.aclose()
        with anyio.fail_after(2):
            while client.is_connected:
                await anyio.sleep(0.01)
        assert client.is_connected is False


async def test_clean_aclose_is_not_logged_as_death(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with _stub_harness():
        pass  # harness calls aclose() — intentional close, not death
    assert "connection lost" not in caplog.text
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/mcp/test_client.py -k "is_connected or stream_close or clean_aclose" -v`
Expected: FAIL — `AttributeError: 'McpClient' object has no attribute 'is_connected'` (first test) and `AttributeError` / no death logging path for the others.

- [ ] **Step 4: Implement death detection in `client.py`**

Add module imports (extend existing blocks): `import logging`, `from types import TracebackType`, and `from mcp.shared.message import SessionMessage`. After `__all__` add:

```python
logger = logging.getLogger(__name__)
```

Add the proxy class at module level (before `class McpClient`):

```python
class _MonitoredReadStream:
    """Delegating wrapper that reports unexpected stream end as server death.

    Implements the subset of the anyio receive-stream interface the SDK
    session uses (``receive``, async iteration, ``aclose``, async context
    manager). On ``EndOfStream``, a closed/broken resource, or an
    ``Exception`` item — the signals SDK ``stdio_client`` emits when the
    subprocess dies — calls ``on_death(reason)`` once, then forwards the
    signal unchanged so SDK behavior is untouched.
    """

    def __init__(
        self,
        inner: MemoryObjectReceiveStream[SessionMessage | Exception],
        on_death: Callable[[str], None],
    ) -> None:
        self._inner = inner
        self._on_death = on_death
        self._reported = False

    def _report(self, reason: str) -> None:
        if not self._reported:
            self._reported = True
            self._on_death(reason)

    async def receive(self) -> SessionMessage | Exception:
        try:
            item = await self._inner.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError, anyio.BrokenResourceError) as exc:
            self._report(f"read stream {type(exc).__name__}")
            raise
        if isinstance(item, Exception):
            self._report(f"read stream delivered {type(item).__name__}: {item}")
        return item

    def __aiter__(self) -> AsyncIterator[SessionMessage | Exception]:
        return self

    async def __anext__(self) -> SessionMessage | Exception:
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        await self._inner.aclose()

    async def __aenter__(self) -> "_MonitoredReadStream":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()
```

In `McpClient.__init__`, after `self._notification_senders: ... = []` add:

```python
        self._connected = False
        self._closing = False
        self._death_reason: str | None = None
```

Replace `McpClient.connect` entirely with:

```python
    async def connect(self, config: ServerConfig) -> None:
        """Open transport + session and run the MCP initialize handshake."""
        if self._session is not None:
            raise McpError("Already connected — call aclose() first")
        self._death_reason = None
        stack = AsyncExitStack()
        try:
            streams = await stack.enter_async_context(self._transport_factory(config))
            monitored = _MonitoredReadStream(streams.read, self._on_transport_death)
            session = await stack.enter_async_context(
                ClientSession(
                    cast(
                        MemoryObjectReceiveStream[SessionMessage | Exception],
                        monitored,
                    ),
                    streams.write,
                    # ``*args`` absorbs both SDK handler conventions, so it
                    # can't structurally match the two-arg Protocol — cast.
                    message_handler=cast(
                        MessageHandlerFnT, self._on_inbound_message
                    ),
                )
            )
            with anyio.fail_after(self._settings.request_timeout_seconds):
                init = await session.initialize()
        except TimeoutError as exc:
            await stack.aclose()
            raise McpTimeoutError(
                f"initialize handshake timed out after "
                f"{self._settings.request_timeout_seconds}s"
            ) from exc
        except (FileNotFoundError, PermissionError) as exc:
            await stack.aclose()
            raise McpConnectionError(f"Could not spawn server process: {exc}") from exc
        except Exception:
            # Config, transport, or handshake failure — unwind the stack
            # before re-raising so a half-open connection never leaks.
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        self._connected = True
        self._server_info = ServerInfo(
            name=init.serverInfo.name,
            version=init.serverInfo.version,
            # The SDK types protocolVersion as ``str | int`` (legacy drafts
            # used integers); normalize to str for Octave's typed boundary.
            protocol_version=str(init.protocolVersion),
        )
```

Add the death handler method (place after `connect`, before `aclose`):

```python
    def _on_transport_death(self, reason: str) -> None:
        """Monitor callback: mark disconnected, record the reason, log.

        Runs inside the SDK receive loop, so it cannot await — it does NOT
        unwind the exit stack; ``aclose()``/``restart()`` do the teardown.
        Suppressed while ``aclose()`` is tearing down intentionally.
        """
        if self._closing:
            return  # intentional teardown, not a death
        self._connected = False
        self._death_reason = reason
        logger.warning("MCP server connection lost | reason=%s", reason)
```

Replace `McpClient.aclose` entirely with:

```python
    async def aclose(self) -> None:
        """Unwind the connection (terminate subprocess / close HTTP). Idempotent.

        Sets the closing flag first so the read-stream monitor treats the
        teardown as an intentional close, not a subprocess death.
        """
        self._closing = True
        self._connected = False
        stack = self._stack
        self._stack = None
        self._session = None
        self._server_info = None
        self._death_reason = None
        if stack is not None:
            await stack.aclose()
        self._closing = False
```

Add the property (after `aclose`, before `server_info`):

```python
    @property
    def is_connected(self) -> bool:
        """True between a successful ``connect()`` and ``aclose()`` or death.

        Cheap synchronous flag — no I/O. Observers (health checks, UI) poll
        this instead of pinging; callers learn of death via
        ``McpConnectionError``.
        """
        return self._connected
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/mcp/test_client.py -v`
Expected: all PASS (new and pre-existing).

Run: `cd backend && uv run mypy src/ && uv run ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add backend/src/octave/mcp/client.py backend/tests/mcp/test_client.py
git commit -m "feat(mcp): detect transport stream death and expose is_connected"
```

---

### Task 2: Post-death calls raise `McpConnectionError`

**Files:**
- Modify: `src/octave/mcp/client.py` (`_run`, `connect` except-clauses, `aclose`)
- Modify: `src/octave/mcp/errors.py` (docstring)
- Test: `tests/mcp/test_client.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/mcp/test_client.py`:

```python
async def test_death_via_exception_item_fails_calls_with_connection_error() -> None:
    async with _stub_harness() as (client, swrite):
        # SDK stdio_client forwards subprocess failures as Exception items
        # on the read stream — the reason its type is SessionMessage | Exception.
        await swrite.send(RuntimeError("server process exited unexpectedly"))
        with anyio.fail_after(2):
            while client.is_connected:
                await anyio.sleep(0.01)
        with pytest.raises(McpConnectionError):
            await client.ping()
        with pytest.raises(McpConnectionError):
            await client.list_tools()


async def test_calls_after_stream_close_raise_connection_error() -> None:
    async with _stub_harness() as (client, swrite):
        await swrite.aclose()
        with anyio.fail_after(2):
            while client.is_connected:
                await anyio.sleep(0.01)
        with pytest.raises(McpConnectionError):
            await client.list_tools()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/mcp/test_client.py -k "exception_item or after_stream_close" -v`
Expected: FAIL — calls after death currently raise `McpNotConnectedError`/raw `ClosedResourceError`/`BrokenResourceError` (leaked past the boundary), not `McpConnectionError`; teardown may also surface SDK teardown noise from `aclose()`.

- [ ] **Step 3: Implement the boundary translation**

In `client.py`, replace `_run` entirely with:

```python
    async def _run(self, operation: str, awaitable: Awaitable[Any]) -> Any:
        """Await ``awaitable`` under the request timeout; translate failures.

        An SDK ``McpError`` (a JSON-RPC error response on the wire) becomes
        ``McpRpcError`` carrying the code verbatim; timeout becomes
        ``McpTimeoutError``; a dead transport becomes ``McpConnectionError``.
        Callers only ever catch Octave types.
        """
        if not self._connected:
            raise McpConnectionError(
                f"cannot {operation}: server connection lost "
                f"({self._death_reason or 'reason unknown'}); call restart()"
            )
        try:
            with anyio.fail_after(self._settings.request_timeout_seconds):
                return await awaitable
        except (anyio.ClosedResourceError, anyio.BrokenResourceError) as exc:
            raise McpConnectionError(
                f"cannot {operation}: server connection closed ({exc})"
            ) from exc
        except TimeoutError as exc:
            raise McpTimeoutError(
                f"{operation} timed out after {self._settings.request_timeout_seconds}s"
            ) from exc
        except SdkMcpError as exc:
            raise McpRpcError(
                exc.error.message, code=exc.error.code, data=exc.error.data
            ) from exc
```

In `connect`, insert this except clause immediately **before** the final `except Exception:` clause (so an `McpConnectionError` raised inside the try passes through after stack cleanup, without re-wrapping):

```python
        except McpError:
            await stack.aclose()
            raise
```

In `aclose`, make teardown best-effort — a dying connection surfaces SDK
child-task crashes at context exit; the connection is going away regardless,
so log instead of masking the caller's flow. Replace the `await stack.aclose()`
line inside `aclose` with:

```python
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:
                # Teardown noise from a connection that is already dying —
                # the SDK surfaces child-task crashes at context exit. The
                # connection is going away regardless; don't mask the caller.
                logger.debug(
                    "MCP connection teardown error suppressed", exc_info=True
                )
```

In `errors.py`, extend the `McpConnectionError` docstring to:

```python
class McpConnectionError(McpError):
    """The server could not be reached, was spawned but died mid-session,
    or dropped unexpectedly. ``restart()`` may recover a dead connection."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/mcp/test_client.py -v`
Expected: all PASS. (If the pre-existing `test_ping_before_connect_raises_not_connected` / `test_methods_after_aclose_raise_not_connected` break, check ordering: `_require_session` must still raise `McpNotConnectedError` **before** `_run`'s connected-check — never-connected / after-`aclose` means `_session is None`.)

Run: `cd backend && uv run mypy src/ && uv run ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add backend/src/octave/mcp/client.py backend/src/octave/mcp/errors.py backend/tests/mcp/test_client.py
git commit -m "feat(mcp): raise McpConnectionError for calls on a dead connection"
```

---

### Task 3: Fail-fast for in-flight requests (cancel-scope registry)

**Files:**
- Modify: `src/octave/mcp/client.py` (`__init__`, `_on_transport_death`, `_run`, `connect` initialize section)
- Test: `tests/mcp/test_client.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/mcp/test_client.py`:

```python
async def test_inflight_request_fails_fast_when_server_dies() -> None:
    async with _stub_harness(request_timeout=30.0) as (client, swrite):
        error: BaseException | None = None

        async def _call() -> None:
            nonlocal error
            try:
                await client.ping()  # stub peer never answers pings
            except BaseException as exc:  # noqa: E722 — asserted below
                error = exc

        async def _die_after_a_moment() -> None:
            await anyio.sleep(0.05)
            await swrite.aclose()  # subprocess death mid-request

        with anyio.move_on_after(5) as watchdog:
            async with anyio.create_task_group() as tg:
                tg.start_soon(_call)
                tg.start_soon(_die_after_a_moment)

        # Death must abort the in-flight call in milliseconds, not wait for
        # the 30 s request timeout: the watchdog must NOT have fired.
        assert watchdog.cancelled_caught is False
        assert isinstance(error, McpConnectionError)


async def test_death_during_initialize_raises_connection_error() -> None:
    @asynccontextmanager
    async def _dead_on_arrival(
        _config: ServerConfig,
    ) -> AsyncIterator[TransportStreams]:
        # The "subprocess" dies before answering initialize: the peer's send
        # stream is closed immediately, so the first read hits EOF.
        async with create_client_server_memory_streams() as (
            (cread, cwrite),
            (_sread, swrite),
        ):
            await swrite.aclose()
            yield TransportStreams(read=cread, write=cwrite)

    client = McpClient(transport_factory=_dead_on_arrival)
    with anyio.move_on_after(5) as watchdog:
        with pytest.raises(McpConnectionError):
            await client.connect(StdioConfig(command="dead-on-arrival"))
    assert watchdog.cancelled_caught is False  # died fast, not a 30 s timeout
    assert client.is_connected is False


async def test_external_cancellation_is_not_converted() -> None:
    async with _stub_harness() as (client, _swrite):
        with anyio.move_on_after(0.5) as scope:
            await client.ping()  # never answered; the caller cancels
        # Genuine caller cancellation must propagate as cancellation, not be
        # swallowed and converted to McpConnectionError.
        assert scope.cancelled_caught is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/mcp/test_client.py -k "inflight or dead_on or external_cancellation" -v`
Expected: `test_inflight_request_fails_fast_when_server_dies` FAIL — `watchdog.cancelled_caught is True` (the call hangs until the 5 s watchdog, proving no fail-fast); `test_death_during_initialize_raises_connection_error` FAIL — `McpTimeoutError` instead of `McpConnectionError` (or hang). `test_external_cancellation_is_not_converted` may already pass; keep it as a regression pin.

- [ ] **Step 3: Implement the cancel-scope registry**

In `McpClient.__init__`, after `self._death_reason: str | None = None` add:

```python
        self._request_scopes: set[anyio.CancelScope] = set()
```

In `_on_transport_death`, append the cancellation loop after the `logger.warning(...)` line:

```python
        for scope in list(self._request_scopes):
            scope.cancel()
```

Replace `McpClient._run` entirely with:

```python
    async def _run(self, operation: str, awaitable: Awaitable[Any]) -> Any:
        """Await ``awaitable`` under timeout + death-cancel scope; translate failures.

        An SDK ``McpError`` (a JSON-RPC error response on the wire) becomes
        ``McpRpcError`` carrying the code verbatim; timeout becomes
        ``McpTimeoutError``; a dead transport (death-cancelled scope, or a
        closed/broken stream) becomes ``McpConnectionError``. Genuine outer
        cancellation propagates untouched — only scopes this client cancelled
        are converted. Callers only ever catch Octave types.
        """
        if not self._connected:
            raise McpConnectionError(
                f"cannot {operation}: server connection lost "
                f"({self._death_reason or 'reason unknown'}); call restart()"
            )
        scope = anyio.CancelScope()
        self._request_scopes.add(scope)
        try:
            with scope:
                with anyio.fail_after(self._settings.request_timeout_seconds):
                    result = await awaitable
            if scope.cancelled_caught:
                raise McpConnectionError(
                    f"cannot {operation}: server process exited "
                    f"({self._death_reason or 'reason unknown'})"
                )
            return result
        except (anyio.ClosedResourceError, anyio.BrokenResourceError) as exc:
            raise McpConnectionError(
                f"cannot {operation}: server connection closed ({exc})"
            ) from exc
        except TimeoutError as exc:
            raise McpTimeoutError(
                f"{operation} timed out after {self._settings.request_timeout_seconds}s"
            ) from exc
        except SdkMcpError as exc:
            raise McpRpcError(
                exc.error.message, code=exc.error.code, data=exc.error.data
            ) from exc
        finally:
            self._request_scopes.discard(scope)
```

In `connect`, replace the handshake-await block:

```python
            with anyio.fail_after(self._settings.request_timeout_seconds):
                init = await session.initialize()
```

with:

```python
            init = await self._await_initialize(session)
```

and add the helper method (after `_on_transport_death`):

```python
    async def _await_initialize(self, session: ClientSession) -> InitializeResult:
        """Run the handshake under a death-cancellable scope + timeout.

        If the subprocess dies mid-handshake the monitor cancels the scope;
        the death surfaces as ``McpConnectionError`` instead of a misleading
        ``McpTimeoutError`` after the full request timeout.
        """
        scope = anyio.CancelScope()
        self._request_scopes.add(scope)
        try:
            with scope:
                with anyio.fail_after(self._settings.request_timeout_seconds):
                    init = await session.initialize()
        finally:
            self._request_scopes.discard(scope)
        if scope.cancelled_caught:
            raise McpConnectionError(
                f"Server process exited during initialize: {self._death_reason}"
            )
        return init
```

Add `InitializeResult` to the existing `mcp.types` import in `client.py`:

```python
from mcp.types import ClientNotification, ClientRequest, InitializeResult
```

(keep the existing `TextContent as SdkTextContent` import line as-is).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/mcp/test_client.py -v`
Expected: all PASS — including `test_slow_tool_times_out_and_session_survives` (the timeout path must be unaffected: `fail_after`'s `TimeoutError` passes through the death scope untouched, `cancelled_caught` stays False).

Run: `cd backend && uv run mypy src/ && uv run ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add backend/src/octave/mcp/client.py backend/tests/mcp/test_client.py
git commit -m "feat(mcp): abort in-flight requests fast when the server dies"
```

---

### Task 4: `restart()` from the stored config

**Files:**
- Modify: `src/octave/mcp/client.py` (`__init__`, `connect`, `restart`)
- Test: `tests/mcp/test_client.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/mcp/test_client.py`. Add the import at the top (the package `tests/mcp/__init__.py` exists, so the relative import resolves):

```python
from .conftest import build_server
```

Then append:

```python
@asynccontextmanager
async def _reconnectable_harness() -> AsyncIterator[tuple[McpClient, list[int]]]:
    """Client whose factory spawns a fresh in-process server per call.

    Mirrors what restart() does against a real subprocess: every factory
    call is a fresh spawn with its own server, streams, and handshake.
    Yields (client, spawn-log).
    """
    spawns: list[int] = []

    @asynccontextmanager
    async def _factory(_config: ServerConfig) -> AsyncIterator[TransportStreams]:
        spawns.append(len(spawns))
        server = build_server()
        async with create_client_server_memory_streams() as (
            (cread, cwrite),
            (sread, swrite),
        ):
            async with anyio.create_task_group() as tg:
                tg.start_soon(
                    server.run, sread, swrite, server.create_initialization_options()
                )
                try:
                    yield TransportStreams(read=cread, write=cwrite)
                finally:
                    tg.cancel_scope.cancel()

    client = McpClient(transport_factory=_factory)
    await client.connect(StdioConfig(command="in-process"))
    try:
        yield client, spawns
    finally:
        await client.aclose()


async def test_restart_reconnects_from_stored_config() -> None:
    async with _reconnectable_harness() as (client, spawns):
        assert spawns == [0]
        assert client.is_connected is True
        await client.restart()
        assert client.is_connected is True
        assert spawns == [0, 1]  # a fresh spawn ran
        tools = await client.list_tools()
        assert [tool.name for tool in tools] == ["echo", "slow", "tool_error"]
    assert client.is_connected is False


async def test_restart_respawn_failure_keeps_client_dead_and_retryable() -> None:
    attempts: list[int] = []

    @asynccontextmanager
    async def _flaky_factory(
        _config: ServerConfig,
    ) -> AsyncIterator[TransportStreams]:
        attempts.append(1)
        if len(attempts) == 2:
            # The binary "went away" between restarts.
            raise FileNotFoundError(2, "No such file or directory")
        async with create_client_server_memory_streams() as (
            (cread, cwrite),
            (sread, swrite),
        ):
            async with anyio.create_task_group() as tg:
                tg.start_soon(_answer_initialize_only, sread, swrite)
                try:
                    yield TransportStreams(read=cread, write=cwrite)
                finally:
                    tg.cancel_scope.cancel()

    client = McpClient(transport_factory=_flaky_factory)
    await client.connect(StdioConfig(command="flaky"))
    assert client.is_connected is True

    with pytest.raises(McpConnectionError):
        await client.restart()  # second spawn fails
    assert client.is_connected is False
    assert len(attempts) == 2

    await client.restart()  # third spawn succeeds — config retained
    assert client.is_connected is True
    await client.aclose()


async def test_restart_before_connect_raises_not_connected() -> None:
    with pytest.raises(McpNotConnectedError):
        await McpClient().restart()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/mcp/test_client.py -k "restart" -v`
Expected: FAIL — `AttributeError: 'McpClient' object has no attribute 'restart'`.

- [ ] **Step 3: Implement config storage + restart**

In `McpClient.__init__`, after `self._connected = False` add:

```python
        self._config: ServerConfig | None = None
```

In `connect`, in the success block, add `self._config = config` immediately before `self._connected = True` so the block reads:

```python
        self._stack = stack
        self._session = session
        self._config = config
        self._connected = True
```

Add `restart` after `aclose` (before `is_connected`):

```python
    async def restart(self) -> None:
        """Manually restart the connection: teardown, respawn, re-handshake.

        Uses the config from the last successful ``connect()``. Spawn or
        handshake failures raise (normally ``McpConnectionError``) with the
        client left disconnected and the config retained, so ``restart()``
        is retryable. Lifecycle operations are single-caller by contract —
        the same convention as ``connect()``/``aclose()``.
        """
        if self._config is None:
            raise McpNotConnectedError(
                "cannot restart: connect() has never been called"
            )
        config = self._config
        await self.aclose()
        try:
            await self.connect(config)
        except McpError:
            logger.exception("MCP server restart failed")
            raise
        logger.info("MCP server connection restarted")
```

Do **not** clear `_config` in `aclose` — retention is what makes restart after intentional close possible.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/mcp/test_client.py -v`
Expected: all PASS.

Run: `cd backend && uv run mypy src/ && uv run ruff check src/ tests/`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add backend/src/octave/mcp/client.py backend/tests/mcp/test_client.py
git commit -m "feat(mcp): add manual restart() from the stored server config"
```

---

### Task 5: Integration — real subprocess exit → restart

**Files:**
- Create: `tests/mcp/fixtures/dying_server.py`
- Modify: `tests/mcp/test_stdio_integration.py`

- [ ] **Step 1: Create the dying-server fixture**

Create `tests/mcp/fixtures/dying_server.py`:

```python
"""Stdio MCP server that can kill itself, for exit-capture tests.

Spawned by tests/mcp/test_stdio_integration.py: ``echo`` proves a working
connection, ``exit_now`` terminates the process (os._exit, no cleanup)
mid-request so the test observes exit capture and restart against the
real SDK stdio_client subprocess.
"""

import os

from mcp.server.fastmcp import FastMCP

server = FastMCP("dying-fixture")


@server.tool()
def echo(text: str) -> str:
    """Echo the input text back."""
    return text


@server.tool()
def exit_now() -> str:
    """Terminate the process without answering the request."""
    os._exit(3)


if __name__ == "__main__":
    server.run()
```

- [ ] **Step 2: Write the failing integration test**

In `tests/mcp/test_stdio_integration.py`, extend the imports (keep existing lines, add):

```python
import pytest

from octave.mcp.config import McpSettings
from octave.mcp.errors import McpConnectionError
```

and add after `_ECHO_SERVER`:

```python
_DYING_SERVER = Path(__file__).parent / "fixtures" / "dying_server.py"
```

then append the test:

```python
async def test_subprocess_exit_is_captured_and_restart_recovers() -> None:
    client = McpClient(settings=McpSettings(request_timeout_seconds=10.0))
    try:
        await client.connect(
            StdioConfig(command=sys.executable, args=[str(_DYING_SERVER)])
        )
        assert client.is_connected is True

        # The server kills itself mid-call: the in-flight request must fail
        # fast with an Octave error, not hang until the 10 s timeout.
        with pytest.raises(McpConnectionError):
            await client.call_tool("exit_now")
        assert client.is_connected is False

        # Subsequent calls fail the same way until a restart.
        with pytest.raises(McpConnectionError):
            await client.ping()

        # Manual restart respawns the subprocess and re-runs the handshake.
        await client.restart()
        assert client.is_connected is True
        result = await client.call_tool("echo", {"text": "reborn"})
        assert result.content[0].text == "reborn"
    finally:
        await client.aclose()
```

- [ ] **Step 3: Run the integration test**

Run: `cd backend && uv run pytest tests/mcp/test_stdio_integration.py -v`
Expected: both tests PASS (the new one exercises Tasks 1–4 against a real subprocess; if `OCTAVE_MCP_SKIP_SUBPROCESS_TESTS=1` is set in the environment, unset it for this run — the test must actually execute).

- [ ] **Step 4: Run the full MCP suite + quality gates**

Run: `cd backend && uv run pytest tests/mcp/ -v && uv run mypy src/ && uv run ruff check src/ tests/`
Expected: all PASS, no lint/type errors.

- [ ] **Step 5: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add backend/tests/mcp/fixtures/dying_server.py backend/tests/mcp/test_stdio_integration.py
git commit -m "test(mcp): cover subprocess exit capture and restart end-to-end"
```

---

### Task 6: Docs + full verification

**Files:**
- Modify: `docs/ARCHITECTURE.md:70`
- Modify: `docs/TODO.md:76`

- [ ] **Step 1: Update `docs/ARCHITECTURE.md`**

Replace the MCP Connector bullet:

```markdown
- **Server Lifecycle Manager** — Start, stop, restart, and health-monitor connected MCP servers
```

with:

```markdown
- **Connection Lifecycle** — Start and manual restart (`McpClient.restart()`) with `is_connected` liveness; subprocess exit detected via transport-stream monitoring (fail-fast `McpConnectionError`). Auto-restart policy and health monitoring land with the server lifecycle manager (roadmap #4)
```

- [ ] **Step 2: Update `docs/TODO.md`**

Replace:

```markdown
- [ ] 2. Add MCP stdio transport support (spawn subprocess servers) — reduced by PR #81 to config persistence + Settings UI wiring; transport exists behind `open_transport`
```

with:

```markdown
- [x] 2. Add MCP stdio transport support (spawn subprocess servers) — PR #81 (transport behind `open_transport`) + PR #91 (subprocess exit capture, manual restart); config persistence + Settings UI wiring deferred to #7/UI
```

- [ ] **Step 3: Full verification**

Run: `cd backend && uv run pytest tests/ -v && uv run mypy src/ && uv run ruff check src/ tests/`
Expected: full backend suite PASS; mypy and ruff clean.

- [ ] **Step 4: Commit and push**

```bash
cd "$(git rev-parse --show-toplevel)"
git add docs/ARCHITECTURE.md docs/TODO.md
git commit -m "docs(mcp): mark stdio transport complete, note lifecycle split with roadmap #4"
git push
```

Expected: PR #91 updates with the new commits.

---

## Self-Review

- **Spec coverage:** exit detection via monitor proxy (Task 1) ✓; exception-item path (Task 2) ✓; fail-fast in-flight + initialize (Task 3) ✓; cancellation hygiene (Task 3) ✓; `is_connected` (Task 1) ✓; manual `restart()` incl. retry-after-failure + never-connected guard (Task 4) ✓; logging WARN-death/INFO-restart (Tasks 1, 4) ✓; `McpConnectionError` docstring (Task 2) ✓; integration exit→restart with `dying_server.py` (Task 5) ✓; docs/TODO updates (Task 6) ✓. Non-goals (auto-restart, exit codes, stderr, transport.py changes) deliberately absent per spec.
- **Placeholder scan:** every code step contains full code; every command has an expected outcome. No TBD/similar-to-N.
- **Type consistency:** `_MonitoredReadStream(inner, on_death)`, `_on_transport_death(reason: str)`, `_request_scopes: set[anyio.CancelScope]`, `_await_initialize(session) -> InitializeResult`, `is_connected -> bool`, `restart() -> None`, `_answer_initialize_only`, `_stub_harness`, `_reconnectable_harness` — names/signatures identical across tasks and tests.
