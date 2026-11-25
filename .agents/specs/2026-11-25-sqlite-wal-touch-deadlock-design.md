# Design: SQLite WAL Write-Upgrade Deadlock Fix (issue #161)

**Date:** 2026-11-25
**Branch:** `fix/sqlite-wal-write-upgrade-deadlock` · **PR:** #162
**Status:** Approved (brainstorming complete)

## Problem

Every authenticated request resolves its cookie through
`AuthService.resolve_session` → `AuthStore.get_by_token_hash` (a SELECT, which
starts the root transaction as a deferred `BEGIN` via the explicit-BEGIN
listener in `octave/db/_bootstrap.py:attach_transaction_control`) and then
`AuthStore.touch_session` (an UPDATE on the same transaction). In WAL mode,
upgrading a read transaction to a write after another connection committed
since the snapshot fails **immediately** with `SQLITE_BUSY_SNAPSHOT` — a class
of `SQLITE_BUSY` that `PRAGMA busy_timeout` does not wait on. Result:
concurrent authed requests 500 with `sqlite3.OperationalError: database is
locked` (repro: 3 backgrounded `curl -b cookie localhost:8010/api/sessions`).

Aggravating factor: read-only routes (e.g. `GET /sessions`) never commit, so
the pending touch UPDATE squats on the write lock for the whole request and
rolls back at teardown — pure contention, zero durable effect.

The same read→write upgrade pattern exists in the expired-row delete-on-read
sweep in `get_by_token_hash`.

## Constraints

- **Issue #27 convention preserved:** stores never commit; callers own
  transaction boundaries. The store gains no new commit paths.
- **No global `BEGIN IMMEDIATE`:** the WS chat-turn transaction
  (`websocket/connection.py::_handle_user_message`) spans LLM inference;
  immediate locking would serialize every reader behind multi-second turn
  transactions.
- Sliding-window expiry is **advisory precision by design** (documented in
  `auth/deps.py`): a dropped or delayed touch is acceptable; a failed request
  is not.

## Decision

**Pure-read request path + out-of-band, best-effort, throttled touch.**

1. **`AuthStore.get_by_token_hash` — no more delete-on-read.** An expired row
   returns `None`; the row is left untouched. Expired rows die in
   `AuthService.login`'s `purge_expired` sweep (unchanged). Removes the last
   write from the resolve read path.

2. **`AuthService.resolve_session` — pure read.** Drops the `touch_session`
   call. After this change the entire HTTP request transaction is
   write-free unless the route itself writes.

3. **`AuthStore.touch_session_by_hash(token_hash, *, now, idle_ttl_days,
   absolute_ttl_days, grace_seconds) -> int`** replaces
   `touch_session(row, ...)`. One statement, no preceding read (a read would
   reintroduce the upgrade inside the touch transaction itself):

   ```sql
   UPDATE auth_sessions
      SET last_seen_at = :now,
          expires_at = MIN(:now + idle_ttl, datetime(created_at, '+abs_ttl days'))
    WHERE id = :hash
      AND last_seen_at < :now - grace_seconds
   ```

   - The `MIN` of the idle expiry (Python-computed bind) against the absolute
     cap (`func.datetime` over the `created_at` column) keeps the cap logic
     in one statement. String comparison over SQLite-stored ISO datetimes is
     chronological; SQLAlchemy's DATETIME result parsing tolerates the
     missing fractional seconds `datetime()` emits.
   - The `grace` clause (default 60s) makes most touches zero-row no-ops —
     under WAL, a zero-row UPDATE takes no write lock contention beyond the
     (already serialized) begin, and more importantly the common case writes
     nothing.
   - Returns rowcount (1 = slid, 0 = throttled or unknown hash). Store still
     never commits.

4. **`octave/auth/sliding.py` — out-of-band executor.**
   `async def slide_session(factory, raw_token, *, settings) -> bool`:
   opens a session from `db_session_factory`, runs
   `touch_session_by_hash` through `AuthStore`, commits (this helper *is* the
   caller that owns the transaction — the #27 convention is respected, not
   bypassed), returns whether the slide applied. Posture:
   - `asyncio.wait_for(..., settings.touch_timeout_seconds)` — **awaited with
     a short timeout** (user-approved tweak, default `2.0s`; must stay below
     the 5s `busy_timeout`). A cancelled touch rolls back via the session
     context manager.
   - Every exception is swallowed with a `logger.warning` — expiry sliding
     must never fail or delay a request beyond the timeout.
   - Missing factory → return `False` (mirrors the peripheral posture in
     `mcp/lifespan.py`).

5. **Call sites.**
   - `auth/deps.py::get_current_user` — after a valid resolution, `await
     slide_session(request.app.state.db_session_factory, token, settings=...)`
     before returning the user. The 401 paths never touch.
   - `websocket/connection.py::_resolve_identity` — same call; the now
     pointless `await session.commit()` (the only writer in that transient
     transaction was the touch) is removed, leaving a pure read.

6. **`AuthSettings` gains two env-backed fields** (`OCTAVE_AUTH_*`):
   `touch_grace_seconds: int = 60`, `touch_timeout_seconds: float = 2.0`.

## Alternatives considered

- **Global `BEGIN IMMEDIATE` in `attach_transaction_control`** — rejected:
  the LLM-spanning chat-turn transaction would serialize all readers behind
  it; catastrophic at far lower load than the repro.
- **Throttle only, write in band** — rejected: shrinks collision frequency by
  ~99% but every fired touch retains the upgrade failure mode; mitigation,
  not a fix. (Throttling is kept inside this design as a free optimization.)
- **Fire-and-forget task for the touch** — superseded by user decision:
  await with short timeout instead (bounded latency, observable failures,
  simpler tests).

## Residual risk (follow-up issue, out of scope)

Mutating routes still open with an auth/identity SELECT and write later
(`POST /sessions`, login, admin routes). The window between snapshot and
first write is milliseconds, and the only long holder is the WS turn
transaction (different connection, commits at turn end). Under WAL the
upgrade-fails-fast path remains reachable at extreme load. Document as a
follow-up issue (rollback-and-retry for route-level transactions) — recorded,
not fixed here.

## Testing

1. **Concurrency regression test (the repro):** file-backed engine through
   the real FastAPI app over `httpx.ASGITransport`; login once, then N=10
   concurrent `GET /api/sessions` while sibling requests run committed
   writes (`POST /sessions`). Assert zero 500s. Must be verified to fail on
   pre-fix code (TDD red).
2. **Deterministic store test:** two raw connections on one SQLite file —
   conn A explicit `BEGIN` + `SELECT` (acquires snapshot), conn B `UPDATE` +
   `COMMIT`, conn A then executes the *new* resolve path (read-only) and must
   raise nothing; contrast test documents that an in-band UPDATE on A's
   upgraded transaction raises `database is locked` (pins the mechanism).
3. **Store unit tests (rewrite existing):** expired → `None` with row
   present afterwards; `touch_session_by_hash` slides expiry, honors the
   absolute cap in SQL, throttles within grace (rowcount 0, `last_seen_at`
   unchanged), unknown hash → 0.
4. **Service tests:** `resolve_session` leaves the session `not is_dirty()`
   and emits no DML (after rollback, DB `last_seen_at` unchanged).
5. **`sliding.py` tests:** happy path slides + commits; busy/failing DB is
   swallowed (returns `False`, logs warning); timeout cancels without
   raising; missing factory → `False`.
6. **WS test update:** handshake no longer commits a slide (pure read).
7. Existing suites (`test_store.py`, `test_service.py`, route/WS tests)
   updated for the removed `touch_session(row, ...)` signature.
