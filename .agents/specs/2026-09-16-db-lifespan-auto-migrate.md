# DB Lifespan & Auto-Migrate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire `octave.db` into the FastAPI lifespan — build the adapter from `DatabaseSettings`, auto-migrate and ensure the vector store at startup, publish `db_session_factory` on `app.state`, and make `/api/health` report real DB status.

**Architecture:** New `octave/db/lifespan.py` exports one async context manager (`db_lifespan`) that `app.py` plugs in via `FastAPI(lifespan=...)`. Fail-fast posture: any startup DB failure propagates and the process exits. Health becomes an aggregate check registry (`CHECKS` map) so future components (inference, MCP) slot in beside `db`. Spec: [`.agents/specs/2026-09-16-db-lifespan-auto-migrate-design.md`](2026-09-16-db-lifespan-auto-migrate-design.md).

**Tech Stack:** Python 3.12, FastAPI `lifespan=`, SQLAlchemy 2.0 async + aiosqlite, Alembic (sync `upgrade`/`current` wrappers), pytest (`asyncio_mode=auto`), uv.

**Work item:** #85 · **Branch:** `feature/db-lifespan-auto-migrate` · **Draft PR:** [#88](https://github.com/Svagtlys/Octave/pull/88)

**Commands run from `backend/`** (workspace root is the repo root, so: `cd backend && uv run pytest -q`). Gates per task: `uv run pytest -q`, `uv run ruff check src tests`, `uv run mypy src`.

**Key existing APIs you'll consume (already built, do not modify):**

- `octave.db.registry.default_registry.create(config) -> DbAdapter` — resolves `config.adapter` (name or import string); raises `UnknownDbAdapterError` for unknown names.
- `octave.db.migrations.upgrade(sync_url)` / `current(sync_url) -> str | None` — sync Alembic wrappers; `upgrade` is idempotent, `current` returns `None` for an unmigrated DB.
- `octave.db.adapter.DbAdapter` — `make_engine() -> AsyncEngine`, `make_session_factory(engine)`, `ensure_vector_store(connection, *, dim=None)` (caller owns the transaction), `aclose()`.
- `octave.db.config.DbConfig.sync_url` — `sqlite+aiosqlite://` → `sqlite://` mirror for Alembic.
- `octave.db.deps.get_db_session` — reads `app.state.db_session_factory`; this plan populates it. **Do not touch `deps.py`.**
- `octave.db.sqlite_adapter.vector_table_name(dim) -> str` — `vec_vault_items_<dim>` (test helper).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `backend/src/octave/db/config.py` | Modify | Add `auto_migrate` flag to `DatabaseSettings` (env `OCTAVE_DB_AUTO_MIGRATE`, default `true`). `DbConfig` unchanged. |
| `backend/tests/db/test_config.py` | Modify | Two tests for the new flag. |
| `backend/src/octave/db/lifespan.py` | Create | `db_lifespan(app)` — startup wiring + teardown, fail-fast. |
| `backend/src/octave/db/__init__.py` | Modify | Re-export `db_lifespan`. |
| `backend/tests/db/test_lifespan.py` | Create | Lifespan behavior: migrate, gate, fail-fast, restart idempotency. |
| `backend/src/octave/app.py` | Modify | Pass `lifespan=db_lifespan` to `FastAPI(...)`. |
| `backend/src/octave/routes/health.py` | Modify | Aggregate `CHECKS` registry + `_check_db`; keep `/version` untouched. |
| `backend/tests/test_health.py` | Rewrite | Aggregate shape: 200/503, ping failure, global-app contract. |
| `.agents/context/architecture.md` | Modify | Data Layer bullet: auto-migration is now wired. |

---

### Task 1: `auto_migrate` flag on `DatabaseSettings`

**Files:**
- Modify: `backend/src/octave/db/config.py` (class `DatabaseSettings`, after line 57 `embedding_dim`)
- Test: `backend/tests/db/test_config.py`

Context: `DatabaseSettings` is a `pydantic_settings.BaseSettings` with `env_prefix="OCTAVE_DB_"` — a new field `auto_migrate` is automatically backed by `OCTAVE_DB_AUTO_MIGRATE`. Tests construct with `_env_file=None` so a developer's local `.env` can't leak in (existing convention in this test file). The flag does **not** go on `DbConfig` (adapter-facing connection config only — spec Decision 1); `test_to_db_config_projects_fields` already proves `DbConfig` stays a 3-field frozen dataclass.

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/db/test_config.py`:

```python
def test_auto_migrate_defaults_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTAVE_DB_AUTO_MIGRATE", raising=False)
    assert DatabaseSettings(_env_file=None).auto_migrate is True


def test_auto_migrate_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTAVE_DB_AUTO_MIGRATE", "false")
    assert DatabaseSettings(_env_file=None).auto_migrate is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/db/test_config.py -q`
Expected: 2 FAIL with `AttributeError: 'DatabaseSettings' object has no attribute 'auto_migrate'` (the `is False` test fails identically — attribute missing).

- [ ] **Step 3: Write minimal implementation** — in `backend/src/octave/db/config.py`, inside `DatabaseSettings` directly after the `embedding_dim` field (line 57):

```python
    auto_migrate: bool = True
    """Apply pending migrations at startup. ``False`` skips the upgrade but
    still verifies the DB is migrated (see ``octave.db.lifespan``)."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/db/test_config.py -q`
Expected: all PASS (9 passed).

- [ ] **Step 5: Gate + commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add src/octave/db/config.py tests/db/test_config.py
git commit -m "feat(db): add auto_migrate flag to DatabaseSettings"
```

---

### Task 2: `octave/db/lifespan.py` — startup wiring

**Files:**
- Create: `backend/src/octave/db/lifespan.py`
- Modify: `backend/src/octave/db/__init__.py`
- Test: `backend/tests/db/test_lifespan.py`

Context: one public export, `db_lifespan`, decorated with `@asynccontextmanager` — FastAPI accepts it directly as `lifespan=`. Sequence (spec Decision 2): settings → `to_db_config()` → `default_registry.create()` → migrate-or-verify → `make_engine()` → `engine.begin()` + `ensure_vector_store()` → publish `db_adapter`/`db_engine`/`db_session_factory` on `app.state` → `yield` → `finally`: `aclose()` + `engine.dispose()` on every path, including the crash path. `upgrade`/`current` are sync (Alembic drives a sync engine) — safe to call directly because the lifespan runs before uvicorn accepts connections.

- [ ] **Step 1: Write the failing tests** — create `backend/tests/db/test_lifespan.py`:

```python
"""db_lifespan: startup wiring, auto-migrate gate, fail-fast, teardown."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, inspect

from octave.db.errors import DbMigrationError, UnknownDbAdapterError
from octave.db.lifespan import db_lifespan
from octave.db.migrations import current, upgrade
from octave.db.sqlite_adapter import vector_table_name


@pytest.fixture
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Hermetic OCTAVE_DB_* env: chdir so no local .env leaks in, clear the
    vars, then point the URL at a temp file. Yields the database path."""
    monkeypatch.chdir(tmp_path)
    for var in (
        "OCTAVE_DB_URL",
        "OCTAVE_DB_ADAPTER",
        "OCTAVE_DB_EMBEDDING_DIM",
        "OCTAVE_DB_AUTO_MIGRATE",
    ):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "life.db"
    monkeypatch.setenv("OCTAVE_DB_URL", f"sqlite+aiosqlite:///{path}")
    yield path


async def test_startup_migrates_ensures_vector_store_and_populates_state(
    db_env: Path,
) -> None:
    app = FastAPI()
    async with db_lifespan(app):
        assert current(f"sqlite:///{db_env}") is not None
        assert app.state.db_session_factory is not None
        assert app.state.db_adapter is not None
        assert app.state.db_engine is not None
    # Vector index created at the configured dim; relational schema too.
    engine = create_engine(f"sqlite:///{db_env}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert vector_table_name(768) in tables
    assert {"users", "vault_items"} <= tables


async def test_auto_migrate_false_unmigrated_raises(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTAVE_DB_AUTO_MIGRATE", "false")
    app = FastAPI()
    with pytest.raises(DbMigrationError, match="alembic upgrade head"):
        async with db_lifespan(app):
            pass  # never reached


async def test_auto_migrate_false_pre_migrated_boots_without_upgrade(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sync_url = f"sqlite:///{db_env}"
    upgrade(sync_url)
    monkeypatch.setenv("OCTAVE_DB_AUTO_MIGRATE", "false")

    def _explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("upgrade() must not run when auto_migrate=false")

    monkeypatch.setattr("octave.db.lifespan.upgrade", _explode)
    app = FastAPI()
    async with db_lifespan(app):
        assert app.state.db_session_factory is not None


async def test_unknown_adapter_propagates(
    db_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OCTAVE_DB_ADAPTER", "no_such_adapter")
    app = FastAPI()
    with pytest.raises(UnknownDbAdapterError):
        async with db_lifespan(app):
            pass  # never reached


async def test_restart_is_idempotent(db_env: Path) -> None:
    """Full start → stop → start on the same file. Proves teardown released
    the engine (no leaked handle) and setup is idempotent (upgrade() is
    Alembic-idempotent; ensure_vector_store() sees the existing index)."""
    for _ in range(2):
        app = FastAPI()
        async with db_lifespan(app):
            assert app.state.db_session_factory is not None
    assert current(f"sqlite:///{db_env}") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/db/test_lifespan.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'octave.db.lifespan'`.

- [ ] **Step 3: Write the implementation** — create `backend/src/octave/db/lifespan.py`:

```python
"""Application lifespan: build the DB adapter, migrate, publish state.

Fail-fast posture (spec Decision 5): any startup failure propagates so the
process exits — serving traffic against an untrusted schema is worse than
a crash. The ``finally`` block releases adapter and engine on every path,
including the crash path, so no file handle leaks.

``upgrade``/``current`` are sync (Alembic drives a sync engine). That is
fine here: the lifespan runs before uvicorn accepts connections, so the
brief blocking costs nothing. If the loop ever matters, wrap in
``anyio.to_thread.run_sync`` — do not defer the wiring for it.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from octave.db.config import DatabaseSettings
from octave.db.errors import DbMigrationError
from octave.db.migrations import current, upgrade
from octave.db.registry import default_registry

__all__ = ["db_lifespan"]

logger = logging.getLogger(__name__)


@asynccontextmanager
async def db_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wire the database for the app's lifetime.

    Startup: settings -> adapter -> migrations (gated by
    ``settings.auto_migrate``) -> engine -> vector store -> ``app.state``.
    Shutdown: ``adapter.aclose()`` + ``engine.dispose()``.
    """
    settings = DatabaseSettings()
    config = settings.to_db_config()
    adapter = default_registry.create(config)
    engine: AsyncEngine | None = None
    try:
        try:
            if settings.auto_migrate:
                upgrade(config.sync_url)
            elif current(config.sync_url) is None:
                raise DbMigrationError(
                    "database not migrated — run `alembic upgrade head` "
                    "or set OCTAVE_DB_AUTO_MIGRATE=true"
                )
            engine = adapter.make_engine()
            async with engine.begin() as connection:
                await adapter.ensure_vector_store(connection)
        except Exception:
            logger.exception("database startup failed; failing fast")
            raise
        app.state.db_adapter = adapter
        app.state.db_engine = engine
        app.state.db_session_factory = adapter.make_session_factory(engine)
        logger.info(
            "database ready: adapter=%s dim=%s",
            config.adapter,
            config.embedding_dim,
        )
        yield
    finally:
        await adapter.aclose()
        if engine is not None:
            await engine.dispose()
```

Note the nested try: the `except` logs only genuine *startup* failures (an exception raised at the `yield` is a runtime failure and must not be logged as "startup failed"), while the outer `finally` guarantees cleanup on every path.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/db/test_lifespan.py -q`
Expected: 5 passed.

- [ ] **Step 5: Re-export from the package** — in `backend/src/octave/db/__init__.py`, add the import after the `errors` import line (`from octave.db.errors import DbError`):

```python
from octave.db.lifespan import db_lifespan
```

and add `"db_lifespan",` to `__all__` (alphabetically, after `"current",`).

- [ ] **Step 6: Gate + commit**

```bash
cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src
git add src/octave/db/lifespan.py src/octave/db/__init__.py tests/db/test_lifespan.py
git commit -m "feat(db): add db_lifespan startup wiring with auto-migrate gate"
```

---

### Task 3: Wire `db_lifespan` into the app

**Files:**
- Modify: `backend/src/octave/app.py`

Context: `ASGITransport` and context-less `TestClient` do **not** run lifespan handlers, so existing tests that import the global `app` (`test_health.py`, `test_cors.py`, `test_error_handling.py`, `test_websocket.py`) are unaffected — they never trigger startup. Only `with TestClient(app):`-style usage or uvicorn runs the lifespan. This task adds no new test; the lifespan is tested in isolation in Task 2, and the full suite proves nothing regressed.

- [ ] **Step 1: Modify `backend/src/octave/app.py`** — full new content:

```python
from fastapi import FastAPI

from octave.db.lifespan import db_lifespan
from octave.middleware import LogRequestMiddleware, add_cors, add_error_handlers
from octave.routes.health import router as health_router
from octave.websocket.connection import router as ws_router

app = FastAPI(title="Octave Backend", lifespan=db_lifespan)

# Middleware
add_cors(app)
add_error_handlers(app)
app.add_middleware(LogRequestMiddleware)

# Routes
app.include_router(health_router, prefix="/api")
app.include_router(ws_router)
```

- [ ] **Step 2: Run the full suite to verify no regression**

Run: `cd backend && uv run pytest -q`
Expected: all pass (lifespan does not fire under `ASGITransport`/non-context `TestClient`).

- [ ] **Step 3: Gate + commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add src/octave/app.py
git commit -m "feat(app): run db_lifespan on startup"
```

---

### Task 4: Aggregate health endpoint with DB check

**Files:**
- Modify: `backend/src/octave/routes/health.py`
- Test: `backend/tests/test_health.py` (full rewrite)

Context: `/api/health` becomes the single aggregate endpoint (spec Decision 4): a `CHECKS` map of named check functions, overall verdict `ok`/`degraded`, `200` when all checks pass else `503`. The DB check runs `SELECT 1` through `app.state.db_session_factory` — **never** `migrations.current()`, which opens a fresh sync engine per call (too heavy for the 30s compose healthcheck). Checks never raise; failures log `WARN` and return `{"status": "unavailable"}`. `/version` stays untouched. Consumers unchanged: compose `urlopen` and the frontend `res.ok` both key off the status code.

- [ ] **Step 1: Rewrite the failing tests** — replace `backend/tests/test_health.py` entirely:

```python
"""Aggregate health: per-component checks, overall verdict, status codes."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from octave.app import app as global_app
from octave.routes.health import router


def _probe_app() -> FastAPI:
    """Fresh app with only the health router — no lifespan, no shared state."""
    probe = FastAPI()
    probe.include_router(router, prefix="/api")
    return probe


async def _get(app: FastAPI) -> tuple[int, dict]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")
    return response.status_code, response.json()


@pytest.fixture
def working_factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Empty but valid SQLite file — SELECT 1 works on any file."""
    engine = create_engine(f"sqlite:///{tmp_path / 'health.db'}")
    engine.dispose()
    return async_sessionmaker(
        create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'health.db'}"),
        expire_on_commit=False,
    )


@pytest.mark.asyncio
async def test_health_ok_when_db_up(
    working_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = _probe_app()
    app.state.db_session_factory = working_factory
    status, body = await _get(app)
    assert status == 200
    assert body == {"status": "ok", "db": {"status": "ok"}}


@pytest.mark.asyncio
async def test_health_503_when_factory_unset() -> None:
    status, body = await _get(_probe_app())
    assert status == 503
    assert body == {"status": "degraded", "db": {"status": "unavailable"}}


@pytest.mark.asyncio
async def test_health_503_when_ping_fails() -> None:
    factory = async_sessionmaker(
        create_async_engine("sqlite+aiosqlite:////nonexistent-dir-42/x.db"),
        expire_on_commit=False,
    )
    app = _probe_app()
    app.state.db_session_factory = factory
    status, body = await _get(app)
    assert status == 503
    assert body == {"status": "degraded", "db": {"status": "unavailable"}}


@pytest.mark.asyncio
async def test_global_app_reports_db_down_without_lifespan() -> None:
    """Contract: ASGITransport never runs the lifespan, so the global app
    has no session factory — health must honestly report degraded."""
    status, body = await _get(global_app)
    assert status == 503
    assert body["status"] == "degraded"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_health.py -q`
Expected: all 4 FAIL — the current static endpoint returns `200 {"status": "ok"}` with no `db` key.

- [ ] **Step 3: Implement** — replace `backend/src/octave/routes/health.py` entirely (`/version` unchanged at the bottom):

```python
"""Aggregate health endpoint: per-component checks with an overall verdict.

Status codes carry the signal for monitors (Uptime Kuma, Gatus, compose
HEALTHCHECK, k8s probes); the body serves optional JSON assertions.
Vocabulary is ours (ok/degraded/unavailable) — no monitor requires the
Actuator convention. Adding a component = one check function + one CHECKS
entry. The check-fn contract is where future "optional component must not
degrade the whole app" policy lands (spec Decision 4, deferred).
"""

import logging
import os
from collections.abc import Awaitable, Callable
from importlib import metadata

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)
router = APIRouter()

CheckFn = Callable[[Request], Awaitable[dict[str, str]]]


async def _check_db(request: Request) -> dict[str, str]:
    """SELECT 1 through the shared session factory. Never raises."""
    factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "db_session_factory", None
    )
    if factory is None:
        return {"status": "unavailable"}
    try:
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        logger.warning("database health ping failed", exc_info=True)
        return {"status": "unavailable"}
    return {"status": "ok"}


CHECKS: dict[str, CheckFn] = {"db": _check_db}


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    results = {name: await check(request) for name, check in CHECKS.items()}
    healthy = all(result["status"] == "ok" for result in results.values())
    body: dict[str, object] = {"status": "ok" if healthy else "degraded", **results}
    return JSONResponse(status_code=200 if healthy else 503, content=body)


@router.get("/version")
async def version() -> dict[str, str]:
    try:
        v = metadata.version("octave-backend")
    except metadata.PackageNotFoundError:
        v = "unknown"
    env = os.getenv("OCTAVE_ENV", "development")
    return {"version": v, "env": env}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_health.py -q`
Expected: 4 passed.

- [ ] **Step 5: Run the full suite** (catches any other consumer of the old body shape)

Run: `cd backend && uv run pytest -q`
Expected: all pass. (`test_cors.py` sends `OPTIONS /api/health`; CORSMiddleware answers preflight without reaching the endpoint — unaffected.)

- [ ] **Step 6: Gate + commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add src/octave/routes/health.py tests/test_health.py
git commit -m "feat(health): aggregate health endpoint with db status check"
```

---

### Task 5: Documentation + final verification

**Files:**
- Modify: `.agents/context/architecture.md` (~line 101-102, Data Layer bullet)

- [ ] **Step 1: Update the Data Layer bullet** — replace:

```markdown
- **Alembic** for schema migrations, run programmatically via
  `octave.db.migrations.upgrade()` (startup auto-migration is a separate
  work item)
```

with:

```markdown
- **Alembic** for schema migrations, run programmatically via
  `octave.db.migrations.upgrade()` — auto-applied on app startup via
  `octave.db.lifespan.db_lifespan` unless `OCTAVE_DB_AUTO_MIGRATE=false`
  (then startup verifies the DB is migrated and fails fast if not)
```

- [ ] **Step 2: Full gate**

Run: `cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add .agents/context/architecture.md
git commit -m "docs: record startup auto-migration wiring in architecture notes"
```

---

## Out of scope (reminders, from spec Non-goals)

- Liveness/readiness split; inference/MCP checks (the `CHECKS` map is the seam).
- Contention policy (retry-on-busy, optimistic locking, write queue) — deferred to the event-append work item with the pinned `seq`-inside-the-transaction pattern.
- `docker-compose.yml` / env-file changes — `OCTAVE_DB_AUTO_MIGRATE` defaults true.
- Volume persistence for `octave.db` in compose; structured-logging rework.
