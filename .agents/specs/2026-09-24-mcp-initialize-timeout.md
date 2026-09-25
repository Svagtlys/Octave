# Dedicated Initialize Handshake Timeout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the MCP initialize handshake its own timeout budget (`McpSettings.initialize_timeout_seconds`, default `30.0`, env `OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS`) so tightening the steady-state `request_timeout_seconds` no longer starves slow-to-boot servers.

**Architecture:** One new field on the existing pydantic-settings class `McpSettings`; two call-site swaps in `McpClient` (`_await_initialize()`'s `fail_after` and `connect()`'s timeout error message). `_run()` and the steady-state budget are untouched. Defaults keep the effective handshake budget at 30 s — zero-impact for default settings.

**Tech Stack:** Python 3.12, `anyio` timeouts, `pydantic-settings`, `mcp` SDK 1.x (quarantined behind the façade), pytest (asyncio auto-mode), `uv`.

**Spec:** [`.agents/specs/2026-09-24-mcp-initialize-timeout-design.md`](./2026-09-24-mcp-initialize-timeout-design.md) · **Branch:** `fix/mcp-initialize-timeout` · **Draft PR:** [#102](https://github.com/Svagtlys/Octave/pull/102) · **Issue:** [#101](https://github.com/Svagtlys/Octave/issues/101)

**Conventions:** run all commands from `backend/` with `uv run`. Ruff line-length 88, mypy strict. Do **not** touch `tests/mcp/conftest.py`, `_run()`, `McpServerManager`, or any existing test body — the plan adds new tests only.

---

### Task 1: `McpSettings.initialize_timeout_seconds` field

**Files:**
- Modify: `backend/src/octave/mcp/config.py` (in `McpSettings`, after `request_timeout_seconds`, ~line 64)
- Test: `backend/tests/mcp/test_config.py` (append after `test_settings_env_override`, ~line 47)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/mcp/test_config.py` (after `test_settings_env_override`, before `class TestSupervisionSettings`):

```python
def test_settings_default_initialize_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS", raising=False)
    assert McpSettings().initialize_timeout_seconds == 30.0


def test_settings_env_override_initialize_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS", "8")
    assert McpSettings().initialize_timeout_seconds == 8.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/mcp/test_config.py -v`
Expected: the two new tests FAIL with `AttributeError: 'McpSettings' object has no attribute 'initialize_timeout_seconds'`; all pre-existing tests in the file PASS.

- [ ] **Step 3: Implement the field**

In `backend/src/octave/mcp/config.py`, replace:

```python
    request_timeout_seconds: float = 30.0
```

with:

```python
    request_timeout_seconds: float = 30.0

    initialize_timeout_seconds: float = 30.0
    """Budget for the initialize handshake during connect()/restart().
    Separate from request_timeout_seconds: a slow-to-boot server must not be
    penalized by callers who tighten the steady-state per-request budget."""
```

The env var `OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS` works automatically via
`env_prefix="OCTAVE_MCP_"` — no other wiring needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/mcp/test_config.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/config.py backend/tests/mcp/test_config.py
git commit -m "feat(mcp): add initialize_timeout_seconds setting"
```

---

### Task 2: Run the handshake under the dedicated budget

**Files:**
- Modify: `backend/src/octave/mcp/client.py` (`_await_initialize()` ~line 261; `connect()` timeout message ~lines 192–195)
- Test: `backend/tests/mcp/test_client.py` (append two tests at end of file)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/mcp/test_client.py` (end of file; every import these tests
need — `anyio`, `asynccontextmanager`, `AsyncIterator`, `create_client_server_memory_streams`,
`SessionMessage`, `JSONRPCMessage`/`JSONRPCRequest`/`JSONRPCResponse`, `McpClient`,
`McpSettings`, `ServerConfig`, `StdioConfig`, `McpTimeoutError`, `TransportStreams`,
`_INIT_RESULT` — already exists at module level):

```python
async def test_initialize_timeout_uses_dedicated_budget() -> None:
    """The handshake budget is initialize_timeout_seconds, not the request budget.

    Silent-but-alive peer (never answers initialize); initialize_timeout=0.05,
    request_timeout=1.0. The raised McpTimeoutError must cite 0.05 — proving
    the handshake no longer reads the steady-state knob.
    """

    @asynccontextmanager
    async def _silent_factory(
        _config: ServerConfig,
    ) -> AsyncIterator[TransportStreams]:
        # Memory streams buffer infinitely: the initialize request simply
        # sits unanswered; no peer task needed.
        async with create_client_server_memory_streams() as (
            (cread, cwrite),
            (_sread, _swrite),
        ):
            yield TransportStreams(read=cread, write=cwrite)

    client = McpClient(
        transport_factory=_silent_factory,
        settings=McpSettings(
            initialize_timeout_seconds=0.05, request_timeout_seconds=1.0
        ),
    )
    with pytest.raises(McpTimeoutError) as excinfo:
        await client.connect(StdioConfig(command="silent-peer"))
    assert "0.05" in str(excinfo.value)  # the dedicated budget fired
    assert "1.0" not in str(excinfo.value)  # not the request budget
    assert client.is_connected is False


async def test_slow_initialize_survives_tight_request_timeout() -> None:
    """Regression (#101): a tight request_timeout_seconds must not strangle the
    handshake. Peer boots slowly (answers initialize after 0.2 s) while the
    steady-state budget is 0.05 s; connect() must still succeed on the default
    30 s initialize budget."""

    @asynccontextmanager
    async def _slow_peer_factory(
        _config: ServerConfig,
    ) -> AsyncIterator[TransportStreams]:
        async with create_client_server_memory_streams() as (
            (cread, cwrite),
            (sread, swrite),
        ):

            async def _answer_after_a_moment() -> None:
                message = await sread.receive()
                root = message.message.root
                if isinstance(root, JSONRPCRequest) and root.method == "initialize":
                    await anyio.sleep(0.2)  # slow boot: outlasts request_timeout
                    await swrite.send(
                        SessionMessage(
                            JSONRPCMessage(
                                root=JSONRPCResponse(
                                    jsonrpc="2.0", id=root.id, result=_INIT_RESULT
                                )
                            )
                        )
                    )
                async for _message in sread:
                    pass  # consume notifications/initialized and everything else

            async with anyio.create_task_group() as tg:
                tg.start_soon(_answer_after_a_moment)
                try:
                    yield TransportStreams(read=cread, write=cwrite)
                finally:
                    tg.cancel_scope.cancel()

    client = McpClient(
        transport_factory=_slow_peer_factory,
        settings=McpSettings(request_timeout_seconds=0.05),
    )
    try:
        await client.connect(StdioConfig(command="slow-boot-peer"))
        assert client.is_connected is True
    finally:
        await client.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/mcp/test_client.py -k "initialize_timeout_uses_dedicated_budget or slow_initialize_survives" -v`
Expected: BOTH FAIL —
- `test_initialize_timeout_uses_dedicated_budget`: `McpTimeoutError` message cites `1.0` (old code used the request budget), so `assert "0.05" in str(...)` fails.
- `test_slow_initialize_survives_tight_request_timeout`: `connect()` raises `McpTimeoutError` after 0.05 s (old code applied the request budget to the handshake).

- [ ] **Step 3: Implement the swap**

In `backend/src/octave/mcp/client.py`:

3a. In `_await_initialize()`, replace:

```python
        scope = anyio.CancelScope()
        self._request_scopes.add(scope)
        try:
            with scope:
                with anyio.fail_after(self._settings.request_timeout_seconds):
                    init = await session.initialize()
```

with:

```python
        scope = anyio.CancelScope()
        self._request_scopes.add(scope)
        try:
            with scope:
                with anyio.fail_after(self._settings.initialize_timeout_seconds):
                    init = await session.initialize()
```

Also in that method's docstring, replace the phrase
``McpTimeoutError`` after the full request timeout.
with
``McpTimeoutError`` after the full initialize timeout.

3b. In `connect()`, replace:

```python
            raise McpTimeoutError(
                f"initialize handshake timed out after "
                f"{self._settings.request_timeout_seconds}s"
            ) from exc
```

with:

```python
            raise McpTimeoutError(
                f"initialize handshake timed out after "
                f"{self._settings.initialize_timeout_seconds}s"
            ) from exc
```

Do **not** touch `_run()` — the steady-state path keeps `request_timeout_seconds`.
Do **not** touch the death-cancel scope logic — a subprocess dying mid-handshake
must keep surfacing `McpConnectionError` fast (existing
`test_death_during_initialize_raises_connection_error` guards this).

- [ ] **Step 4: Run the new tests, then the whole client suite**

Run: `cd backend && uv run pytest tests/mcp/test_client.py -v`
Expected: all PASS — the two new tests, plus `test_death_during_initialize_raises_connection_error` (death still beats timeout) and every pre-existing lifecycle test.

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/mcp/client.py backend/tests/mcp/test_client.py
git commit -m "fix(mcp): run initialize handshake under its own timeout budget"
```

---

### Task 3: Env-var documentation

**Files:**
- Modify: `docs/DEVELOPMENT.md` (MCP env-var table, ~lines 206–213)

- [ ] **Step 1: Edit the table**

In `docs/DEVELOPMENT.md`, replace the row:

```markdown
| `OCTAVE_MCP_REQUEST_TIMEOUT_SECONDS` | `30.0` | Per-request timeout for client calls (and the initialize handshake) |
```

with:

```markdown
| `OCTAVE_MCP_REQUEST_TIMEOUT_SECONDS` | `30.0` | Per-request timeout for client calls |
| `OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS` | `30.0` | Budget for the initialize handshake during connect/restart |
```

- [ ] **Step 2: Verify**

Run: `grep -n "OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS" docs/DEVELOPMENT.md`
Expected: exactly one match — the new table row. Also confirm the request-timeout row
no longer contains "(and the initialize handshake)".

- [ ] **Step 3: Commit**

```bash
git add docs/DEVELOPMENT.md
git commit -m "docs(mcp): document OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS"
```

---

### Task 4: Full gate + design/plan docs commit

**Files:**
- Commit: `.agents/specs/2026-09-24-mcp-initialize-timeout-design.md`, `.agents/specs/2026-09-24-mcp-initialize-timeout.md`

- [ ] **Step 1: Full test suite**

Run: `cd backend && uv run pytest`
Expected: all tests PASS. If the sandbox forbids spawning subprocesses, the
`tests/mcp/test_stdio_integration.py` file can be skipped with
`OCTAVE_MCP_SKIP_SUBPROCESS_TESTS=1 uv run pytest` — note the skip in the PR.

- [ ] **Step 2: Lint and type gate**

Run: `cd backend && uv run ruff check . && uv run mypy src`
Expected: `All checks passed!` and no mypy errors.

- [ ] **Step 3: Commit the planning docs**

```bash
git add .agents/specs/2026-09-24-mcp-initialize-timeout-design.md .agents/specs/2026-09-24-mcp-initialize-timeout.md
git commit -m "docs(specs): design and plan for dedicated initialize handshake timeout"
```

- [ ] **Step 4: Push and mark the PR ready for review**

```bash
git push
```

Then update PR #102's description to reference issue #101 (`Closes #101`), summarize the
change (new `OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS` knob, zero-impact default), and list
the test evidence from Steps 1–2.

---

## Self-review notes

- **Spec coverage:** config field + env var (Task 1), client budget + error message (Task 2),
  4 new tests — config default, config env override, handshake-cites-new-knob,
  slow-initialize regression (Tasks 1–2), docs table (Task 3). Out-of-scope items
  (`_run()`, conftest, manager, existing tests) have no tasks by design.
- **Placeholders:** none — every code step is complete, every command has expected output.
- **Type consistency:** field name `initialize_timeout_seconds` identical in config, client,
  tests, docs; env var `OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS` identical in tests and docs
  (derived via `env_prefix="OCTAVE_MCP_"`).
