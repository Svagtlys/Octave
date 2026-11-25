# SQLite WAL Write-Upgrade Deadlock (Issue #161) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the authenticated request path write-free (no deferred-transaction write upgrades → no `SQLITE_BUSY_SNAPSHOT` 500s), moving the session-expiry slide to an out-of-band, best-effort, throttled `UPDATE` that awaits with a short timeout.

**Architecture:** `AuthStore.get_by_token_hash` stops deleting expired rows on read and `AuthService.resolve_session` stops touching the session — the HTTP request transaction becomes pure-read unless the route itself writes. The slide becomes `AuthStore.touch_session_by_hash` (single throttled `UPDATE`, store still never commits) executed by a new `octave/auth/sliding.py` helper that owns its own short transaction (commit — the helper *is* the caller, so the issue #27 convention holds). `get_current_user` and the WS handshake await the helper with `asyncio.wait_for`; every failure is swallowed with a warning.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async + aiosqlite (WAL, `busy_timeout=5000`), pytest (asyncio auto-mode, temp-file SQLite fixtures), uv, ruff, mypy (strict).

**Spec:** [`.agents/specs/2026-11-25-sqlite-wal-touch-deadlock-design.md`](./2026-11-25-sqlite-wal-touch-deadlock-design.md)
**Branch:** `fix/sqlite-wal-write-upgrade-deadlock` · **Draft PR:** https://github.com/Svagtlys/Octave/pull/162

**Commands run from `backend/`.** Facts relied on: `attach_transaction_control` emits deferred `BEGIN` per root transaction (`src/octave/db/_bootstrap.py`); file-backed engines use NullPool (one DBAPI connection per session); `AuthSettings` is pydantic-settings with `env_prefix="OCTAVE_AUTH_"`, `extra="ignore"`; `utcnow()` from `octave.db.models.base`; stores flush but never commit; routes `POST /sessions` and `/api/auth/*` commit.

---

### Task 0: Pre-flight — reproduce at current HEAD (no code changes)

- [ ] **Step 1: Capture the bug deterministically (manual, not committed)**

With the dev backend running on `:8010` (or by booting one), run the issue repro and record the mixed codes:

```bash
cd backend && uv run uvicorn octave.app:app --port 8010 &
# login once, capture cookie, then:
for i in 1 2 3; do curl -s -o /dev/null -w '%{http_code}\n' -b octave_session=$TOKEN \
  'localhost:8010/api/sessions?limit=50&offset=0' & done; wait
```

Expected: at least one `500` among the responses (`database is locked`). If the local server is unavailable, skip — Task 1's deterministic guard reproduces the mechanism in-process. Do not fix anything in this task.

---

### Task 1: `AuthSettings` slide knobs

**Files:**
- Modify: `src/octave/auth/config.py` (after `absolute_ttl_days`)
- Test: `tests/auth/test_config.py` (append at end)

- [ ] **Step 1: Write the failing config tests**

Append to `tests/auth/test_config.py`:

```python
def test_settings_slide_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTAVE_AUTH_TOUCH_GRACE_SECONDS", raising=False)
    monkeypatch.delenv("OCTAVE_AUTH_TOUCH_TIMEOUT_SECONDS", raising=False)
    settings = AuthSettings()
    assert settings.touch_grace_seconds == 60
    assert settings.touch_timeout_seconds == 2.0


def test_settings_slide_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTAVE_AUTH_TOUCH_GRACE_SECONDS", "120")
    monkeypatch.setenv("OCTAVE_AUTH_TOUCH_TIMEOUT_SECONDS", "0.5")
    settings = AuthSettings()
    assert settings.touch_grace_seconds == 120
    assert settings.touch_timeout_seconds == 0.5
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/auth/test_config.py -v`
Expected: both new tests FAIL (`AttributeError` — fields don't exist; env vars ignored via `extra="ignore"`).

- [ ] **Step 3: Add the settings fields**

In `src/octave/auth/config.py` after the `absolute_ttl_days` docstring:

```python
    touch_grace_seconds: int = 60
    """Throttle for the out-of-band session slide (issue #161): an authed
    request only rewrites ``last_seen_at``/``expires_at`` when the stored
    value is older than this. Expiry is advisory-precision by design."""

    touch_timeout_seconds: float = 2.0
    """Max time the request path waits for the out-of-band slide. Must stay
    below the SQLite ``busy_timeout`` (5 s) so a held write lock cancels the
    slide instead of delaying the response."""
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/auth/test_config.py -v` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/octave/auth/config.py tests/auth/test_config.py
git commit -m "feat(auth): add session-slide throttle/timeout settings (issue #161)"
```

---

### Task 2: Deterministic red guards — the request path must emit no DML

These tests pin the *mechanism* (issue #161: read-path transactions must not write). They FAIL against current code and stay green through the refactor.

**Files:**
- Test: `tests/auth/test_service.py` (append at end)

- [ ] **Step 1: Write the two listener-guard tests**

Append to `tests/auth/test_service.py`. The module already provides `_service(session)` and imports `select`, `AuthSession`, `utcnow`, `timedelta` — add `from collections.abc import Callable` and `from sqlalchemy import event` to the top-level imports.

```python
def _capture_writes(statements: list[str]) -> Callable[..., None]:
    """Engine-level before_cursor_execute captures raw SQL text."""

    def _capture(conn, cursor, statement, parameters, context, executemany) -> None:  # noqa: ANN001
        statements.append(statement)

    return _capture


async def test_resolve_session_emits_no_dml(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Issue #161: resolving a valid cookie must not write inside the
    caller's transaction (deferred-BEGIN write upgrades deadlock in WAL)."""
    async with session_factory() as session:
        token = await _service(session).bootstrap_owner("alice", "pw", "Alice")
        await session.commit()
    async with session_factory() as session:
        statements: list[str] = []
        event.listen(
            session.get_bind().sync_engine,
            "before_cursor_execute",
            _capture_writes(statements),
        )
        assert await _service(session).resolve_session(token) is not None
    writes = [s for s in statements if s.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))]
    assert writes == []


async def test_expired_token_read_does_not_delete(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Issue #161: the expired-row sweep must not run inside a read
    transaction — login's purge_expired owns the sweep."""
    async with session_factory() as session:
        token = await _service(session).bootstrap_owner("alice", "pw", "Alice")
        await session.commit()
    async with session_factory() as session:
        row = (await session.execute(select(AuthSession))).scalars().one()
        row.expires_at = utcnow() - timedelta(seconds=1)
        await session.commit()
    async with session_factory() as session:
        statements: list[str] = []
        event.listen(
            session.get_bind().sync_engine,
            "before_cursor_execute",
            _capture_writes(statements),
        )
        assert await _service(session).resolve_session(token) is None
    writes = [s for s in statements if s.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))]
    assert writes == []
```

`session.get_bind()` on the `AsyncSession` returns the `AsyncEngine`; `.sync_engine` is where SQLAlchemy core events attach. Seeds mirror `test_resolve_expired_returns_none` (bootstrap, then age `expires_at`).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/auth/test_service.py -v -k "no_dml or not_delete"`
Expected: both FAIL — the first catches `UPDATE auth_sessions` (touch flush), the second catches `DELETE FROM auth_sessions` (delete-on-read sweep). This is the deterministic red.

- [ ] **Step 3: Commit the red guards**

```bash
git add tests/auth/test_service.py
git commit -m "test(auth): red guards — request-path reads must emit no DML (issue #161)"
```

---

### Task 3: Store + service — pure-read resolve, single-statement slide

**Files:**
- Modify: `src/octave/auth/store.py`
- Modify: `src/octave/auth/service.py` (`resolve_session` only)
- Test: `tests/auth/test_store.py` (replace lines 96–165: the expired-delete test and both `touch_session` tests)

- [ ] **Step 1: Replace the store tests (red)**

In `tests/auth/test_store.py`: delete `test_get_by_token_hash_expired_returns_none_and_deletes`, `test_touch_session_slides_expiry`, `test_touch_session_bounded_by_absolute_cap`. Add:

```python
async def test_get_by_token_hash_expired_returns_none_row_survives(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        session.add(
            AuthSession(
                id="hx", user_id=user.id,
                created_at=now - timedelta(days=100),
                expires_at=now - timedelta(days=1),
                last_seen_at=now - timedelta(days=100),
            )
        )
        await session.commit()
    async with session_factory() as session:
        assert await AuthStore(session).get_by_token_hash("hx", now) is None
        await session.rollback()
    async with session_factory() as session:
        assert await session.get(AuthSession, "hx") is not None  # sweep is login's job


async def test_touch_by_hash_slides_expiry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        await store.create_session(user_id=user.id, token_hash="h1", now=now - timedelta(hours=2))
        await session.commit()
    async with session_factory() as session:
        count = await AuthStore(session).touch_session_by_hash("h1", now=now)
        await session.commit()
        assert count == 1
        row = await session.get(AuthSession, "h1")
        assert row is not None
        assert row.last_seen_at == now
        assert row.expires_at == now + timedelta(days=14)


async def test_touch_by_hash_bounded_by_absolute_cap(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # SQLite strftime truncates to milliseconds; aligning `now` makes the
    # SQL-computed cap round-trip exactly through the DATETIME processor.
    now = utcnow().replace(microsecond=0)
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        session.add(
            AuthSession(
                id="hc", user_id=user.id,
                created_at=now - timedelta(days=89),
                expires_at=now + timedelta(days=1),
                last_seen_at=now - timedelta(days=2),
            )
        )
        await session.commit()
    async with session_factory() as session:
        count = await AuthStore(session).touch_session_by_hash("hc", now=now)
        await session.commit()
        assert count == 1
        row = await session.get(AuthSession, "hc")
        assert row is not None
        # created_at + 90d beats now + 14d here; strftime keeps microseconds
        assert row.expires_at == now - timedelta(days=89) + timedelta(days=90)


async def test_touch_by_hash_throttled_within_grace(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        await store.create_session(user_id=user.id, token_hash="hg", now=now - timedelta(hours=2))
        # simulate a touch 30 s ago
        row = await session.get(AuthSession, "hg")
        assert row is not None
        row.last_seen_at = now - timedelta(seconds=30)
        await session.commit()
    async with session_factory() as session:
        count = await AuthStore(session).touch_session_by_hash("hg", now=now)
        await session.commit()
        assert count == 0
        row = await session.get(AuthSession, "hg")
        assert row is not None
        assert row.last_seen_at == now - timedelta(seconds=30)


async def test_touch_by_hash_unknown_hash_returns_zero(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await AuthStore(session).touch_session_by_hash("nope", now=utcnow()) == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/auth/test_store.py -v`
Expected: new touch tests FAIL with `AttributeError: ... has no attribute 'touch_session_by_hash'`; expired-survives FAILS (row deleted).

- [ ] **Step 3: Rewrite the store methods**

In `src/octave/auth/store.py`:

1. Update imports: add `case, update` to the `sqlalchemy` import; add `timedelta` is already imported.
2. Replace `get_by_token_hash` body (remove the delete):

```python
    async def get_by_token_hash(
        self, token_hash: str, now: datetime
    ) -> AuthSession | None:
        """Valid rows only: an expired row is reported as absent but NOT
        deleted — the read transaction stays write-free (issue #161: a
        delete here upgrades the deferred BEGIN and deadlocks in WAL).
        Expired rows die in login's ``purge_expired``."""
        row = await self._session.get(AuthSession, token_hash)
        if row is None or row.expires_at <= now:
            return None
        return row
```

3. Replace `touch_session` entirely:

```python
    async def touch_session_by_hash(
        self,
        token_hash: str,
        *,
        now: datetime,
        idle_ttl_days: int = 14,
        absolute_ttl_days: int = 90,
        grace_seconds: int = 60,
    ) -> int:
        """Slide idle expiry, never past ``created_at + absolute_ttl_days``.

        One statement, no preceding read: a SELECT-then-UPDATE here would
        reintroduce the WAL write-upgrade deadlock (issue #161) inside the
        slide transaction itself. The grace clause throttles: rows touched
        within ``grace_seconds`` are skipped (rowcount 0, no write lock).
        ``strftime('%...:%f000')`` preserves the 6-fraction-digit shape the
        DATETIME result processor expects. Never commits — callers own the
        transaction.
        """
        idle_expiry = now + timedelta(days=idle_ttl_days)
        abs_cap = func.strftime(
            "%Y-%m-%d %H:%M:%f000",
            AuthSession.created_at,
            f"+{absolute_ttl_days} days",
        )
        result = await self._session.execute(
            update(AuthSession)
            .where(
                AuthSession.id == token_hash,
                AuthSession.last_seen_at < now - timedelta(seconds=grace_seconds),
            )
            .values(
                last_seen_at=now,
                expires_at=case((abs_cap < idle_expiry, abs_cap), else_=idle_expiry),
            )
        )
        return int(_rowcount(result))
```

Note: `strftime`/`case` are SQLite-shaped — acceptable now (SQLite is the only registered adapter); revisit with pgvector (recorded in the spec).

- [ ] **Step 4: Drop the touch from `resolve_session`**

In `src/octave/auth/service.py`, replace the `resolve_session` body's touch call and docstring:

```python
    async def resolve_session(self, token: str) -> ResolvedSession | None:
        """Cookie → identity. ``None`` = anonymous (unknown/expired/disabled).

        Pure read (issue #161): the idle-expiry slide moved out of the
        request transaction to ``octave.auth.sliding``."""
        now = utcnow()
        row = await self._store.get_by_token_hash(hash_token(token), now)
        if row is None:
            return None
        user = await self._store.get_user_by_id(row.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            return None
        return user, row
```

- [ ] **Step 5: Run the auth suite**

Run: `uv run pytest tests/auth -v`
Expected: Task 2 guards now PASS; new store tests PASS; old `resolve_session` tests PASS. `tests/test_websocket.py` may still fail (commit of a now-nonexistent slide is harmless — verify it stays green; it asserts nothing about the slide).

- [ ] **Step 6: Type-check + lint the diff, then commit**

```bash
uv run ruff check src/octave/auth tests/auth && uv run mypy src/octave/auth
git add src/octave/auth/store.py src/octave/auth/service.py tests/auth/test_store.py
git commit -m "fix(auth): pure-read session resolution, single-statement slide (issue #161)"
```

---

### Task 4: `octave/auth/sliding.py` — out-of-band best-effort slide

**Files:**
- Create: `src/octave/auth/sliding.py`
- Test: `tests/auth/test_sliding.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/auth/test_sliding.py`:

```python
"""Out-of-band session slide (issue #161): owns its short transaction,
awaits with a timeout, never fails the caller."""

import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.auth.config import AuthSettings
from octave.auth.passwords import DummyHasher
from octave.auth.service import AuthService
from octave.auth.sliding import slide_session
from octave.auth.store import AuthStore
from octave.db._bootstrap import create_sqlite_engine
from octave.db.models import Base
from octave.db.models.auth import AuthSession
from octave.db.models.base import utcnow


async def _factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'slide.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _login(factory: async_sessionmaker[AsyncSession]) -> str:
    async with factory() as session:
        store = AuthStore(session)
        await store.create_user(username="alice", display_name="Alice", password_hash="dummy$pw")
        await session.commit()
        service = AuthService(store=store, hasher=DummyHasher(), settings=AuthSettings())
        token = await service.login("alice", "pw")
        await session.commit()
        return token


async def test_slide_applies_out_of_band(tmp_path: Path) -> None:
    factory = await _factory(tmp_path)
    token = await _login(factory)
    now = utcnow()
    # age the row past the grace window
    async with factory() as session:
        row = await session.scalar(select(AuthSession))
        assert row is not None
        row.last_seen_at = now - timedelta(minutes=5)
        await session.commit()
    assert await slide_session(factory, token, settings=AuthSettings(), now=now) is True
    async with factory() as session:
        row = await session.scalar(select(AuthSession))
        assert row is not None and row.last_seen_at == now


async def test_slide_throttled_returns_false(tmp_path: Path) -> None:
    factory = await _factory(tmp_path)
    token = await _login(factory)
    assert await slide_session(factory, token, settings=AuthSettings(), now=utcnow()) is False


async def test_slide_never_raises_on_gone_db(tmp_path: Path) -> None:
    factory = await _factory(tmp_path)
    token = await _login(factory)
    # A path whose parent directory does not exist: every connect errors out.
    dead = async_sessionmaker(
        create_sqlite_engine(f"sqlite:///{tmp_path / 'missing' / 'x.db'}")
    )
    assert await slide_session(dead, token, settings=AuthSettings(), now=utcnow()) is False


async def test_slide_timeout_cancels_without_raising(tmp_path: Path) -> None:
    factory = await _factory(tmp_path)
    token = await _login(factory)
    # Hold the write lock from a raw connection beyond the slide timeout.
    # The engine URL carries no path (the async_creator owns it), so derive
    # it from the fixture's own filename.
    db_path = str(tmp_path / "slide.db")
    raw = sqlite3.connect(db_path)
    raw.isolation_level = None
    raw.execute("BEGIN IMMEDIATE")
    raw.execute("UPDATE auth_sessions SET last_seen_at = last_seen_at")
    try:
        settings = AuthSettings(touch_timeout_seconds=0.1)
        assert await slide_session(factory, token, settings=settings, now=utcnow()) is False
    finally:
        raw.rollback()
        raw.close()


async def test_slide_missing_factory_returns_false() -> None:
    assert await slide_session(None, "token", settings=AuthSettings(), now=utcnow()) is False
```

`tmp_path / 'missing' / 'x.db'` errors because the parent directory does not exist. If aiosqlite surfaces connection errors lazily in a way that escapes the helper, that is the bug this test pins.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/auth/test_sliding.py -v`
Expected: collection error / `ModuleNotFoundError: octave.auth.sliding`.

- [ ] **Step 3: Implement the module**

Create `src/octave/auth/sliding.py`:

```python
"""Out-of-band session slide (issue #161).

The request path must stay write-free: an in-band touch upgrades the
deferred BEGIN to a write and deadlocks against WAL readers
(``SQLITE_BUSY_SNAPSHOT``, immune to busy_timeout). This helper opens its
own short-lived session, runs the single throttled UPDATE through
AuthStore, and commits — it IS the caller that owns the transaction, so
the #27 "stores never commit" convention holds.

Posture: best-effort. Awaited with a timeout (below the 5 s busy_timeout)
so a held write lock cancels the slide instead of delaying the request;
every failure is a warning, never an exception — the sliding window is
advisory-precision by design.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from octave.auth.config import AuthSettings
from octave.auth.store import AuthStore
from octave.auth.tokens import hash_token

__all__ = ["slide_session"]

logger = logging.getLogger(__name__)


async def slide_session(
    factory: async_sessionmaker[Any] | None,
    raw_token: str,
    *,
    settings: AuthSettings,
    now: datetime,
) -> bool:
    """Best-effort idle-expiry slide for one session token.

    Returns True when the row was actually slid (throttle passed). Any
    failure — missing factory, busy DB, timeout — returns False and logs;
    a dropped slide only shortens the cookie by <= the grace window.
    """
    if factory is None:
        return False

    async def _slide() -> bool:
        async with factory() as session:
            count = await AuthStore(session).touch_session_by_hash(
                hash_token(raw_token),
                now=now,
                idle_ttl_days=settings.idle_ttl_days,
                absolute_ttl_days=settings.absolute_ttl_days,
                grace_seconds=settings.touch_grace_seconds,
            )
            await session.commit()
            return count > 0

    try:
        return await asyncio.wait_for(_slide(), timeout=settings.touch_timeout_seconds)
    except Exception:
        logger.warning("session slide failed (advisory, ignored)", exc_info=True)
        return False
```

Note: `asyncio.wait_for` raises the builtin `TimeoutError` (an `Exception` subclass, caught by the blanket handler); inner `CancelledError`s stay inside `wait_for` and never reach the caller — which is exactly why the blanket `except Exception` is safe here.

- [ ] **Step 4: Run to verify green**

Run: `uv run pytest tests/auth/test_sliding.py -v` → all PASS.

- [ ] **Step 5: Lint/type, commit**

```bash
uv run ruff check src/octave/auth/sliding.py tests/auth/test_sliding.py && uv run mypy src/octave/auth/sliding.py
git add src/octave/auth/sliding.py tests/auth/test_sliding.py
git commit -m "feat(auth): out-of-band best-effort session slide (issue #161)"
```

---

### Task 5: Wire the slide into HTTP deps and the WS handshake

**Files:**
- Modify: `src/octave/auth/deps.py` (`get_current_user`)
- Modify: `src/octave/websocket/connection.py` (`_resolve_identity`)
- Test: `tests/auth/test_deps.py` (append)

- [ ] **Step 1: Write the failing positive test**

Append to `tests/auth/test_deps.py` (mirrors `test_valid_cookie_resolves_identity`):

```python
async def test_valid_cookie_slides_out_of_band(
    probe_app: FastAPI, factory: async_sessionmaker[AsyncSession]
) -> None:
    """Issue #161: the slide happens on a separate short transaction —
    the request itself succeeds AND last_seen_at advances."""
    from octave.auth.service import AuthService
    from octave.db.models.auth import AuthSession
    from octave.db.models.base import utcnow
    from sqlalchemy import select
    from datetime import timedelta

    async with factory() as session:
        await AuthStore(session).create_user(
            username="alice", display_name="Alice", password_hash="dummy$pw"
        )
        await session.commit()
        service = AuthService(
            store=AuthStore(session), hasher=DummyHasher(), settings=AuthSettings()
        )
        token = await service.login("alice", "pw")
        # age past the grace window
        row = await session.scalar(select(AuthSession))
        assert row is not None
        row.last_seen_at = utcnow() - timedelta(minutes=5)
        await session.commit()
    async with await _client(probe_app) as client:
        client.cookies.set(SESSION_COOKIE, token)
        assert (await client.get("/whoami")).status_code == 200
    async with factory() as session:
        row = await session.scalar(select(AuthSession))
        assert row is not None
        assert row.last_seen_at > utcnow() - timedelta(minutes=5)
```

Note: `probe_app` sets `app.state.auth_settings = AuthSettings()` — the slide reads it via `_settings(request)`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/auth/test_deps.py::test_valid_cookie_slides_out_of_band -v`
Expected: FAIL — `last_seen_at` unchanged (deps no longer touch since Task 3).

- [ ] **Step 3: Wire `get_current_user`**

In `src/octave/auth/deps.py`: add `from octave.auth.sliding import slide_session`; replace the tail of `get_current_user` and its docstring middle paragraph:

```python
    """Resolve the session cookie to an active user, or 401.

    Valid hits slide the idle expiry out of band (issue #161): the request
    transaction stays write-free; the slide is best-effort with its own
    short transaction and a timeout — see ``octave.auth.sliding``."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    resolved = await service.resolve_session(token)
    if resolved is None:
        raise HTTPException(status_code=401, detail="Session invalid or expired")
    await slide_session(
        getattr(request.app.state, "db_session_factory", None),
        token,
        settings=_settings(request),
        now=utcnow(),
    )
    return resolved[0]
```

Import `utcnow` from `octave.db.models.base`.

- [ ] **Step 4: Wire the WS handshake**

In `src/octave/websocket/connection.py::_resolve_identity`, remove `await session.commit()` (the only writer in that transaction was the touch; it is now a pure read) and slide out of band after the `async with` block closes. New body:

```python
async def _resolve_identity(websocket: WebSocket) -> str | None:
    """Cookie -> user id (or None). Pure read (issue #161); the idle-expiry
    slide runs best-effort on its own transaction via ``slide_session``."""
    factory = getattr(websocket.app.state, "db_session_factory", None)
    if factory is None:
        return None
    token = websocket.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    async with factory() as session:
        service = await _service_factory(websocket)(session)
        resolved = await service.resolve_session(token)
    if resolved is None:
        return None
    await slide_session(
        factory, token, settings=_ws_settings(websocket), now=utcnow()
    )
    return resolved[0].id
```

Add imports (`slide_session`, `utcnow`) and a small `_ws_settings(websocket)` helper mirroring `_service_factory`'s settings read: `getattr(websocket.app.state, "auth_settings", None) or AuthSettings()`.

- [ ] **Step 5: Run deps + WS suites**

Run: `uv run pytest tests/auth/test_deps.py tests/test_websocket.py -v` → all PASS (the new test green; WS suite unaffected — it never asserted the slide).

- [ ] **Step 6: Commit**

```bash
git add src/octave/auth/deps.py src/octave/websocket/connection.py tests/auth/test_deps.py
git commit -m "fix(auth): slide session out-of-band in deps and WS handshake (issue #161)"
```

---

### Task 6: Concurrency regression test (the repro) + full verification

**Files:**
- Create: `tests/routes/test_sessions_concurrency.py` (new)

- [ ] **Step 1: Write the concurrency test**

```python
"""Issue #161 repro: concurrent authed reads must never 500 while other
connections commit. Pre-fix: the in-band touch upgrades the deferred read
transaction and dies with sqlite3.OperationalError: database is locked
(SQLITE_BUSY_SNAPSHOT). Post-fix: the request path is write-free."""

from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from octave.auth.config import AuthSettings
from octave.auth.passwords import DummyHasher
from octave.db._bootstrap import create_sqlite_engine
from octave.db.models import Base
from octave.middleware import add_error_handlers
from octave.routes.auth import router as auth_router
from octave.routes.sessions import router as sessions_router


async def _app(tmp_path: Path) -> tuple[FastAPI, str]:
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'conc161.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from sqlalchemy.ext.asyncio import async_sessionmaker

    app = FastAPI()
    app.state.db_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    app.state.auth_hasher = DummyHasher()
    app.state.auth_settings = AuthSettings(cookie_secure=False)
    add_error_handlers(app)
    app.include_router(auth_router, prefix="/api")
    app.include_router(sessions_router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        r = await client.post(
            "/api/auth/bootstrap",
            json={"username": "alice", "password": "pw", "display_name": "Alice"},
        )
        assert r.status_code == 201
        token = r.cookies.get("octave_session", "")
    return app, token


async def test_concurrent_reads_survive_parallel_commits(tmp_path: Path) -> None:
    app, token = await _app(tmp_path)
    headers = {"cookie": f"octave_session={token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        async def get_sessions(i: int) -> int:
            r = await client.get("/api/sessions?limit=50&offset=0", headers=headers)
            return r.status_code

        async def create_session(i: int) -> int:
            r = await client.post("/api/sessions", json={"title": f"s{i}"}, headers=headers)
            return r.status_code

        codes = await asyncio.gather(
            *(get_sessions(i) for i in range(25)),
            *(create_session(i) for i in range(25)),
        )
    reads = codes[:25]
    writes = codes[25:]
    assert set(writes) == {201}
    assert set(reads) == {200}, f"expected no 500s, got {sorted(set(reads))}"
```

Add `import asyncio` at the top and hoist `from sqlalchemy.ext.asyncio import async_sessionmaker` to the module-level imports (delete the local import inside `_app`).

- [ ] **Step 2: Verify it is green post-fix; sanity-check it catches the bug**

Run: `uv run pytest tests/routes/test_sessions_concurrency.py -v` → PASS.
Then, to prove the test bites, temporarily re-introduce the in-band touch: `git stash` is not applicable (already committed); instead confirm mechanism via the Task 2 listener guards (they are the deterministic pins) and, if the dev server repro from Task 0 was recorded, cite it in the PR. Do NOT leave any temporary hack in the tree.

- [ ] **Step 3: Full backend verification**

```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
```

Expected: all green. If a websocket test complains about the removed commit or a service test references `touch_session(`, fix the test to the new contract (do not restore the old API).

- [ ] **Step 4: Commit**

```bash
git add tests/routes/test_sessions_concurrency.py
git commit -m "test(routes): concurrent authed reads survive parallel commits (issue #161 repro)"
```

---

### Task 7: Follow-up issue + PR housekeeping

- [ ] **Step 1: File the follow-up issue** (mutating-route residual window)

Per the spec's Residual-risk section: rollback-and-retry for route-level transactions that SELECT (auth) then write — the millisecond snapshot→first-write upgrade window remains reachable under extreme load (WS turn commits mid-window). Use the `create-work-item` skill conventions (label `needs-triage`).

- [ ] **Step 2: Update PR #162 body**

Summarize: mechanism (deferred BEGIN + WAL upgrade → `SQLITE_BUSY_SNAPSHOT`), fix (pure-read request path, out-of-band throttled slide awaited with 2 s timeout, expired-sweep moved to login-only), preserved convention (stores never commit — `slide_session` is the caller), spec link. Mention the Task 0 repro outcome.

```bash
gh pr edit 162 --body-file <(cat <<'EOF'
...composed body...
EOF
)
```

- [ ] **Step 3: Push**

```bash
git push origin fix/sqlite-wal-write-upgrade-deadlock
```
