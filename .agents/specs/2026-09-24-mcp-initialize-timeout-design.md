# Dedicated Timeout for the MCP Initialize Handshake

**Issue:** [#101](https://github.com/Svagtlys/Octave/issues/101) — bug(mcp): dedicated timeout for initialize handshake
**Branch:** `fix/mcp-initialize-timeout` · **Draft PR:** [#102](https://github.com/Svagtlys/Octave/pull/102)
**Date:** 2026-09-24

## Problem

`McpClient._await_initialize()` ([client.py:250](../../backend/src/octave/mcp/client.py)) runs the
MCP initialize handshake under `anyio.fail_after(self._settings.request_timeout_seconds)` — the
same knob that governs every steady-state request in `_run()`. The two budgets are semantically
different but coupled by accident:

- **Start-up budget** — how long the client waits for a server process to boot and complete the
  handshake (spawn + initialize). Slow on stdio servers (interpreter cold-start).
- **Steady-state budget** — how long a single JSON-RPC call may take. Callers who tighten this
  (e.g. `request_timeout_seconds=0.5` to fail fast on wedged tool calls) unintentionally starve
  the handshake: a server that takes >0.5 s to boot now fails `connect()` with
  `McpTimeoutError`, even though nothing is wedged.

The docs table already documents the smell: `OCTAVE_MCP_REQUEST_TIMEOUT_SECONDS` is described as
"Per-request timeout for client calls *(and the initialize handshake)*".

## Decision

| # | Question | Decision |
|---|---|---|
| 1 | Where does the new budget live? | **New `McpSettings.initialize_timeout_seconds: float = 30.0`** (env `OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS`). Same two-layer config pattern as every other MCP knob; env var comes for free via `env_prefix="OCTAVE_MCP_"`. |
| 2 | Default value | **`30.0`** — identical to the current effective handshake budget (`request_timeout_seconds`'s default), so defaults are zero-impact: no user on default settings sees any behavior change. |
| 3 | Fallback semantics | **Independent value, not `None`-falls-back-to-request-timeout.** An explicit knob with a concrete default is simpler to reason about, matches every sibling field, and the issue specifies `30.0`. Users who *want* coupled budgets set both vars. |
| 4 | Validation | **None.** `McpSettings` has no field validators today; adding a `gt=0` guard for one field is inconsistent style. Negative/zero values behave like `anyio.fail_after` does elsewhere in the codebase (immediate/simultaneous timeout) — same posture as `request_timeout_seconds`. |
| 5 | Test harness plumbing | **None.** New client tests are standalone stub-peer tests in `test_client.py` (the established pattern — see `test_death_during_initialize_raises_connection_error`), constructing `McpSettings` directly. `conftest.py` and `_stub_harness` stay untouched. |
| 6 | Existing tight-budget tests | **Untouched.** `test_client.py:147` (`request_timeout=0.5`), `test_stdio_integration.py:143` (`0.5`), and `FAST` in `test_manager.py` (`0.05`, `FakeClient` overrides `connect()` — no real handshake) are correct *by construction* after the fix: their handshakes get the 30 s default, their request budgets stay tight. |

## Design

### 1. `octave/mcp/config.py`

Add one field to `McpSettings`, placed directly below `request_timeout_seconds` (start-up knob
next to the steady-state knob it replaces for the handshake):

```python
    request_timeout_seconds: float = 30.0

    initialize_timeout_seconds: float = 30.0
    """Budget for the initialize handshake during connect()/restart().
    Separate from request_timeout_seconds: a slow-to-boot server must not be
    penalized by callers who tighten the steady-state per-request budget."""
```

No other config changes. `McpServerManager` needs no change — it passes the same `McpSettings`
instance to every client via `_default_client_factory`, so the new knob propagates automatically.

### 2. `octave/mcp/client.py`

Two call sites, both already existing:

- `_await_initialize()` — `anyio.fail_after(self._settings.initialize_timeout_seconds)`.
  The death-cancel scope wrapper stays exactly as-is: a subprocess that dies mid-handshake
  still surfaces as `McpConnectionError` fast, never waits for the new budget.
- `connect()`'s `except TimeoutError` branch — the `McpTimeoutError` message cites
  `initialize_timeout_seconds` instead of `request_timeout_seconds`, so the reported number
  matches the budget that actually fired.

`_run()` keeps using `request_timeout_seconds` — the steady-state path is untouched.

### 3. Error behavior (unchanged taxonomy)

| Scenario | Before | After |
|---|---|---|
| Handshake exceeds budget | `McpTimeoutError` citing `request_timeout_seconds` | `McpTimeoutError` citing `initialize_timeout_seconds` |
| Server dies mid-handshake | `McpConnectionError` (death scope cancels first) | unchanged |
| Steady-state request exceeds budget | `McpTimeoutError` citing `request_timeout_seconds` | unchanged |
| Defaults everywhere | 30 s handshake budget | 30 s handshake budget (zero-impact) |

### 4. Tests (new only)

`tests/mcp/test_config.py`:

- `test_settings_default_initialize_timeout` — default is `30.0` (delenv-guarded like the
  existing default test).
- `test_settings_env_override_initialize_timeout` — `OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS=8`
  → `8.0`.

`tests/mcp/test_client.py` (standalone stub-peer tests, mirroring the existing
`test_death_during_initialize_raises_connection_error` pattern — no conftest changes):

- `test_initialize_timeout_uses_dedicated_budget` — peer that reads its inbound stream but
  never answers `initialize`; settings `initialize_timeout_seconds=0.05`,
  `request_timeout_seconds=1.0`. Assert `McpTimeoutError` raised, and its message cites
  `0.05` (the new knob) and not `1.0` — proving the handshake no longer reads the request
  budget.
- `test_slow_initialize_survives_tight_request_timeout` — peer that sleeps 0.2 s before
  answering `initialize`; settings `request_timeout_seconds=0.05` (tighter than boot time),
  `initialize_timeout_seconds` left at default. Assert `connect()` succeeds and
  `is_connected` is True — the regression this issue is about: tight request budgets must not
  strangle the handshake.

Existing tests: **no edits** (decision 6).

### 5. Docs

`docs/DEVELOPMENT.md` env-var table:

- New row: `OCTAVE_MCP_INITIALIZE_TIMEOUT_SECONDS` | `30.0` | Budget for the initialize handshake during connect/restart
- Amend the `OCTAVE_MCP_REQUEST_TIMEOUT_SECONDS` row: drop the "(and the initialize handshake)"
  parenthetical → "Per-request timeout for client calls".

## Out of scope

- Per-server handshake budgets (would ride on `ServerConfig`, roadmap #7 config persistence).
- Validation of timeout values (`gt=0` etc.) — decision 4.
- Any change to `_run()`, the manager supervisor, the probe-on-timeout path, or the conftest
  harnesses.
