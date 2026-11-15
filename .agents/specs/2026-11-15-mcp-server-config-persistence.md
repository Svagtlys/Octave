# MCP Server Config Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist MCP server configs (name, transport, endpoint, arguments) in the unified database and load them at startup so Octave boots with its configured fleet.

**Architecture:** A plane-owned `McpConfigStore` (AuthStore pattern, in `octave/mcp/config_store.py`) does CRUD over the existing `mcp_servers` table; `mcp_lifespan` reads enabled rows via `app.state.db_session_factory` when no explicit `configs` are passed. One Alembic revision adds the missing `headers` JSON column. `app.py` is untouched.

**Tech Stack:** Python 3.12, SQLAlchemy 2 async ORM, Alembic, FastAPI lifespan, pytest (asyncio auto mode), uv.

**Spec:** [`.agents/specs/2026-11-15-mcp-server-config-persistence-design.md`](2026-11-15-mcp-server-config-persistence-design.md) · Branch: `feature/mcp-server-config-persistence` · Draft PR: #144 · Closes #21

**Conventions:** All commands run from `backend/`. Tests: `uv run pytest <path> -v`. Lint/type at the end: `uv run ruff check src/ tests/ && uv run mypy src/`. Store contract (repo-wide): stores **never commit** — except the plan's own tests commit explicitly where noted; production callers own transactions.

**Plan-time reconciliation of the spec (already reflected below):** the spec's §2 calls the row→config converter "private". It ships **public** as `to_server_config(row)` and the store gains `list_rows()` (raw ORM rows, same-plane tolerant readers only). Reason: the spec's own §3 startup flow ("convert each row, skip corrupt ones") is impossible if bulk reads convert internally — the lifespan needs per-row conversion to skip one bad row without losing the rest. Strict entry points (`get`, `list_servers`) stay loud per Decision 5. Also: the `mcp_servers` CHECK constraint makes corrupt rows unreachable via normal SQL; tests use `PRAGMA ignore_check_constraints=ON` to exercise the defensive paths.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/octave/db/models/mcp.py` | Modify | Add `headers` JSON column to `McpServer` |
| `src/octave/db/migrations/versions/f2a6c8d0e4b7_mcp_headers.py` | Create | `add_column` revision (head was `d8e5f3a7c2b9`) |
| `tests/db/test_migrations.py` | Modify | Forward-data test: row survives at previous head → headers NULL |
| `src/octave/mcp/errors.py` | Modify | Add `McpServerNameTaken` |
| `src/octave/mcp/config_store.py` | Create | `McpServerEntry`, `to_server_config`, `McpConfigStore` CRUD |
| `tests/mcp/test_config_store.py` | Create | Store tests on real SQLite (shared `engine`/`session_factory` fixtures) |
| `src/octave/mcp/__init__.py` | Modify | Export `McpConfigStore`, `McpServerEntry`, `McpServerNameTaken` |
| `tests/mcp/test_package.py` | Modify | Pin the new public names |
| `src/octave/mcp/lifespan.py` | Modify | `configs=None` default → DB read (enabled rows, skip corrupt) |
| `tests/mcp/test_lifespan.py` | Modify | DB-backed boot tests |
| `docs/TODO.md` | Modify | Check off MCP #7 |

---

### Task 1: `headers` column + migration

**Files:**
- Modify: `src/octave/db/models/mcp.py` (after the `env` column, line ~40)
- Create: `src/octave/db/migrations/versions/f2a6c8d0e4b7_mcp_headers.py`
- Test: `tests/db/test_migrations.py` (append)

- [ ] **Step 1: Write the failing forward-data migration test**

Append to `tests/db/test_migrations.py`:

```python
def test_mcp_server_headers_column_migrates_forward(tmp_path: Path) -> None:
    """A row written at the previous head survives the headers migration."""
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    upgrade(url, revision="d8e5f3a7c2b9")  # head before this work item
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO mcp_servers (id, name, transport, command, enabled, "
                "created_at, updated_at) VALUES "
                "('m_1', 'Legacy', 'stdio', 'old-cmd', 1, "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
    engine.dispose()
    upgrade(url)  # to head (includes the headers column)
    engine = create_engine(url)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT headers FROM mcp_servers WHERE id = 'm_1'")
        ).one()
    engine.dispose()
    assert row.headers is None
```

- [ ] **Step 2: Run it to verify failure**

Run: `uv run pytest tests/db/test_migrations.py::test_mcp_server_headers_column_migrates_forward -v`
Expected: FAIL — `sqlite3.OperationalError: no such column: headers` (raised by the SELECT after `upgrade(url)`; the new revision does not exist yet, so `upgrade(url)` is a no-op and the column is missing).

- [ ] **Step 3: Add the column to the model**

In `src/octave/db/models/mcp.py`, insert directly after the `env` mapped_column (currently line 40):

```python
    headers: Mapped[dict[str, str] | None] = mapped_column(JSON)
    """HTTP auth headers (secrets — never log). stdio rows leave NULL."""
```

- [ ] **Step 4: Write the migration**

Create `src/octave/db/migrations/versions/f2a6c8d0e4b7_mcp_headers.py`:

```python
"""mcp server headers column

Revision ID: f2a6c8d0e4b7
Revises: d8e5f3a7c2b9
Create Date: 2026-11-15
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'f2a6c8d0e4b7'
down_revision: str | None = 'd8e5f3a7c2b9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'mcp_servers',
        sa.Column('headers', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('mcp_servers', 'headers')
```

- [ ] **Step 5: Run the migration tests**

Run: `uv run pytest tests/db/test_migrations.py -v`
Expected: ALL PASS — including `test_migrated_schema_matches_models`, which pins model ↔ migration equivalence (it fails if either Step 3 or Step 4 is missing).

- [ ] **Step 6: Commit**

```bash
git add src/octave/db/models/mcp.py src/octave/db/migrations/versions/f2a6c8d0e4b7_mcp_headers.py tests/db/test_migrations.py .agents/specs/2026-11-15-mcp-server-config-persistence-design.md .agents/specs/2026-11-15-mcp-server-config-persistence.md
git commit -m "feat(db): add headers column to mcp_servers (issue #21)"
```

(First commit on the branch also carries the spec + plan docs.)

---

### Task 2: `McpServerNameTaken` error + `McpConfigStore` create/get/round-trip

**Files:**
- Modify: `src/octave/mcp/errors.py`
- Create: `src/octave/mcp/config_store.py`
- Test: `tests/mcp/test_config_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/mcp/test_config_store.py`:

```python
"""McpConfigStore: CRUD + row conversion over mcp_servers (real SQLite).

Uses the shared engine/session_factory fixtures (tests/conftest.py):
temp-file SQLite through create_sqlite_engine, constraints live.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession

from octave.db.models import McpServer
from octave.mcp.config import HttpConfig, StdioConfig
from octave.mcp.config_store import McpConfigStore, McpServerEntry, to_server_config
from octave.mcp.errors import McpConfigError, McpServerNameTaken

# -- round-trips ---------------------------------------------------------------


async def test_stdio_round_trip(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        entry = await store.create(
            name="files",
            config=StdioConfig(
                command="npx fs-server", args=["--root", "/data"], env={"KEY": "v"}
            ),
        )
        await session.commit()
        assert entry.name == "files"
        assert entry.enabled is True
        assert isinstance(entry.config, StdioConfig)
        assert entry.config.command == "npx fs-server"
        assert entry.config.args == ["--root", "/data"]
        assert entry.config.env == {"KEY": "v"}

    async with session_factory() as session:
        got = await McpConfigStore(session).get(entry.id)
        assert got is not None
        assert got.config == entry.config


async def test_http_round_trip_with_headers(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        entry = await store.create(
            name="remote",
            config=HttpConfig(url="http://h:9/t", headers={"Authorization": "Bearer x"}),
        )
        await session.commit()
    async with session_factory() as session:
        got = await McpConfigStore(session).get(entry.id)
        assert got is not None
        assert isinstance(got.config, HttpConfig)
        assert got.config.url == "http://h:9/t"
        assert got.config.headers == {"Authorization": "Bearer x"}


async def test_null_json_columns_become_dataclass_defaults(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        entry = await McpConfigStore(session).create(
            name="bare", config=StdioConfig(command="cmd")
        )
        await session.commit()
    async with session_factory() as session:
        got = await McpConfigStore(session).get(entry.id)
        assert got is not None
        assert got.config.args == []  # type: ignore[attr-defined]
        assert got.config.env == {}  # type: ignore[attr-defined]


# -- name collision ------------------------------------------------------------


async def test_create_duplicate_name_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        await store.create(name="dup", config=StdioConfig(command="a"))
        with pytest.raises(McpServerNameTaken):
            await store.create(name="dup", config=StdioConfig(command="b"))


# -- get / conversion errors -----------------------------------------------------


def test_to_server_config_rejects_incoherent_rows() -> None:
    # In-memory rows (never flushed): the CHECK constraint makes these
    # unreachable via SQL, so the converter is tested directly.
    with pytest.raises(McpConfigError):
        to_server_config(McpServer(id="x", name="x", transport="carrier-pigeon"))
    with pytest.raises(McpConfigError):
        to_server_config(McpServer(id="x", name="x", transport="stdio", command=None))
    with pytest.raises(McpConfigError):
        to_server_config(McpServer(id="x", name="x", transport="http", url=None))


async def test_get_absent_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await McpConfigStore(session).get("nope") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/mcp/test_config_store.py -v`
Expected: collection FAIL — `ModuleNotFoundError: No module named 'octave.mcp.config_store'`.

- [ ] **Step 3: Add the error class**

In `src/octave/mcp/errors.py`, add `"McpServerNameTaken"` to `__all__` and append:

```python
class McpServerNameTaken(McpError):
    """Another mcp_servers row already owns this unique name."""
```

- [ ] **Step 4: Write the store (create/get/conversion)**

Create `src/octave/mcp/config_store.py`:

```python
"""MCP server config persistence — CRUD over the ``mcp_servers`` table.

Plane-owned store (the AuthStore pattern): one ``AsyncSession`` per instance,
**never commits** — callers own transaction boundaries. The row→ServerConfig
conversion lives here (mcp-plane knowledge); ``octave.db`` never imports this
plane, only the reverse.

``env``/``headers`` may carry secrets: never log values. The redacting
``__repr__`` on StdioConfig/HttpConfig already protects log lines; entries
embed those configs.

Two read surfaces: strict entry reads (``get``/``list_servers`` — loud
``McpConfigError`` on rows that bypassed write-side validation) and raw rows
(``list_rows`` — same-plane tolerant readers, e.g. the lifespan's
skip-corrupt-row boot posture).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from octave.db.models import McpServer
from octave.mcp.config import HttpConfig, ServerConfig, StdioConfig
from octave.mcp.errors import McpConfigError, McpServerNameTaken

__all__ = ["McpConfigStore", "McpServerEntry", "to_server_config"]


@dataclass(frozen=True)
class McpServerEntry:
    """One persisted server, ready to register with the manager."""

    id: str
    name: str
    enabled: bool
    config: ServerConfig


def to_server_config(row: McpServer) -> ServerConfig:
    """Reconstruct the domain config from the transport columns.

    Raises ``McpConfigError`` on rows that bypassed write-side validation
    (unknown transport, stdio without command, http without url) — loud
    beats wrong: a bad connection attempt tells you more than a silent
    repair. Under the shipped CHECK constraint these rows are unreachable
    via SQL; this guards future engines without the constraint.
    """
    if row.transport == "stdio":
        if row.command is None:
            raise McpConfigError(f"mcp server {row.id!r}: stdio row without command")
        return StdioConfig(
            command=row.command, args=list(row.args or []), env=dict(row.env or {})
        )
    if row.transport == "http":
        if row.url is None:
            raise McpConfigError(f"mcp server {row.id!r}: http row without url")
        return HttpConfig(url=row.url, headers=dict(row.headers or {}))
    raise McpConfigError(
        f"mcp server {row.id!r}: unknown transport {row.transport!r}"
    )


def _columns_for(config: ServerConfig) -> dict[str, Any]:
    """Derive the transport columns from the domain config.

    The isinstance branch mirrors the DB CHECK constraint: shape coherence
    is enforced by the type at write time.
    """
    if isinstance(config, StdioConfig):
        return {
            "transport": "stdio",
            "command": config.command,
            "args": list(config.args),
            "env": dict(config.env),
            "url": None,
            "headers": None,
        }
    if isinstance(config, HttpConfig):
        return {
            "transport": "http",
            "command": None,
            "args": None,
            "env": None,
            "url": config.url,
            "headers": dict(config.headers),
        }
    raise McpConfigError(f"unsupported config type: {type(config).__name__}")


def _to_entry(row: McpServer) -> McpServerEntry:
    return McpServerEntry(
        id=row.id, name=row.name, enabled=row.enabled, config=to_server_config(row)
    )


class McpConfigStore:
    """CRUD over ``mcp_servers``. One session per instance; never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, name: str, config: ServerConfig, enabled: bool = True
    ) -> McpServerEntry:
        """Insert one server. Raises ``McpServerNameTaken`` on collision."""
        if await self._get_by_name(name) is not None:
            raise McpServerNameTaken(name)
        row = McpServer(
            id=uuid.uuid4().hex, name=name, enabled=enabled, **_columns_for(config)
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:  # race between check and insert
            raise McpServerNameTaken(name) from exc
        return _to_entry(row)

    async def get(self, server_id: str) -> McpServerEntry | None:
        row = await self._session.get(McpServer, server_id)
        return None if row is None else _to_entry(row)

    async def list_rows(self, *, enabled_only: bool = False) -> Sequence[McpServer]:
        """Raw ORM rows, oldest first — for same-plane tolerant readers
        (the lifespan converts per row and skips corrupt ones)."""
        stmt = select(McpServer).order_by(McpServer.created_at, McpServer.id)
        if enabled_only:
            stmt = stmt.where(McpServer.enabled.is_(True))
        return (await self._session.execute(stmt)).scalars().all()

    async def _get_by_name(self, name: str) -> McpServer | None:
        result = await self._session.execute(
            select(McpServer).where(McpServer.name == name)
        )
        return result.scalars().first()
```

- [ ] **Step 5: Run to verify pass**

Run: `uv run pytest tests/mcp/test_config_store.py -v`
Expected: ALL PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add src/octave/mcp/errors.py src/octave/mcp/config_store.py tests/mcp/test_config_store.py
git commit -m "feat(mcp): McpConfigStore create/get with typed transport conversion"
```

---

### Task 3: Store update / delete / list / set_enabled + never-commits contract

**Files:**
- Modify: `src/octave/mcp/config_store.py`
- Test: `tests/mcp/test_config_store.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/mcp/test_config_store.py`:

```python
# -- update / delete / set_enabled / list --------------------------------------


async def test_update_replaces_only_provided_fields(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        entry = await store.create(name="svc", config=StdioConfig(command="a"))
        updated = await store.update(entry.id, config=HttpConfig(url="http://h:1/m"))
        assert updated is not None
        assert isinstance(updated.config, HttpConfig)
        assert updated.name == "svc"  # untouched
        assert updated.enabled is True
        await session.commit()
    async with session_factory() as session:
        got = await McpConfigStore(session).get(entry.id)
        assert got is not None
        assert got.config == HttpConfig(url="http://h:1/m")


async def test_update_transport_swaps_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        entry = await store.create(
            name="flip", config=HttpConfig(url="http://h/m", headers={"A": "1"})
        )
        back = await store.update(entry.id, config=StdioConfig(command="x", args=["-q"]))
        assert back is not None
        assert isinstance(back.config, StdioConfig)
        assert back.config.args == ["-q"]
        await session.commit()
    async with session_factory() as session:
        row = await session.get(McpServer, entry.id)
        assert row is not None
        assert row.headers is None  # http columns cleared by the swap
        assert row.transport == "stdio"


async def test_update_name_collision_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        await store.create(name="alpha", config=StdioConfig(command="a"))
        beta = await store.create(name="beta", config=StdioConfig(command="b"))
        with pytest.raises(McpServerNameTaken):
            await store.update(beta.id, name="alpha")


async def test_update_absent_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await McpConfigStore(session).update("nope", enabled=False) is None


async def test_delete_returns_existence(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        entry = await store.create(name="gone", config=StdioConfig(command="a"))
        assert await store.delete(entry.id) is True
        assert await store.delete(entry.id) is False
        assert await store.get(entry.id) is None


async def test_set_enabled_toggles(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        entry = await store.create(name="toggle", config=StdioConfig(command="a"))
        assert await store.set_enabled(entry.id, False) is True
        assert await store.set_enabled("nope", True) is False
        await session.commit()
    async with session_factory() as session:
        got = await McpConfigStore(session).get(entry.id)
        assert got is not None and got.enabled is False


async def test_list_orders_by_created_at_then_id(
    session_factory: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    async with engine.begin() as conn:
        for sid, created in (
            ("z_old", "2026-01-01 00:00:00"),
            ("m_mid", "2026-01-01 00:00:00"),  # same instant: id tiebreak
            ("a_new", "2026-01-02 00:00:00"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO mcp_servers (id, name, transport, command, enabled, "
                    "created_at, updated_at) VALUES (:i, :n, 'stdio', 'cmd', 1, :c, :c)"
                ),
                {"i": sid, "n": sid, "c": created},
            )
    async with session_factory() as session:
        entries = await McpConfigStore(session).list_servers()
        assert [e.id for e in entries] == ["m_mid", "z_old", "a_new"]


async def test_list_enabled_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        await store.create(name="on", config=StdioConfig(command="a"))
        await store.create(name="off", config=StdioConfig(command="b"), enabled=False)
        await session.commit()
    async with session_factory() as session:
        enabled = await McpConfigStore(session).list_servers(enabled_only=True)
        assert [e.name for e in enabled] == ["on"]
        every = await McpConfigStore(session).list_servers()
        assert len(every) == 2


# -- transaction contract --------------------------------------------------------


async def test_store_never_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An uncommitted store write is invisible to a second session."""
    async with session_factory() as session:
        await McpConfigStore(session).create(name="quiet", config=StdioConfig(command="a"))
        # no commit
    async with session_factory() as session:
        assert await McpConfigStore(session).list_servers() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/mcp/test_config_store.py -v`
Expected: FAIL — `AttributeError: 'McpConfigStore' object has no attribute 'update'` (round-trip tests from Task 2 still pass).

- [ ] **Step 3: Implement the remaining verbs**

Add to `McpConfigStore` in `src/octave/mcp/config_store.py` (after `get`):

```python
    async def update(
        self,
        server_id: str,
        *,
        name: str | None = None,
        config: ServerConfig | None = None,
        enabled: bool | None = None,
    ) -> McpServerEntry | None:
        """Replace exactly the provided fields (``None`` = leave unchanged).
        A provided ``config`` replaces the whole transport column set.
        Returns ``None`` if the id is absent."""
        row = await self._session.get(McpServer, server_id)
        if row is None:
            return None
        if name is not None and name != row.name:
            if await self._get_by_name(name) is not None:
                raise McpServerNameTaken(name)
            row.name = name
        if config is not None:
            for column, value in _columns_for(config).items():
                setattr(row, column, value)
        if enabled is not None:
            row.enabled = enabled
        try:
            await self._session.flush()
        except IntegrityError as exc:  # race between check and flush
            raise McpServerNameTaken(name if name is not None else row.name) from exc
        return _to_entry(row)

    async def delete(self, server_id: str) -> bool:
        """Remove one row. Idempotent — absent is not an error."""
        row = await self._session.get(McpServer, server_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def list_servers(self, *, enabled_only: bool = False) -> list[McpServerEntry]:
        """Strict entry read (loud on corrupt rows). Startup uses
        ``list_rows`` + per-row conversion to skip bad rows instead."""
        return [
            _to_entry(row)
            for row in await self.list_rows(enabled_only=enabled_only)
        ]

    async def set_enabled(self, server_id: str, enabled: bool) -> bool:
        """Targeted enable/disable. Returns existence."""
        row = await self._session.get(McpServer, server_id)
        if row is None:
            return False
        row.enabled = enabled
        await self._session.flush()
        return True
```

- [ ] **Step 4: Run the full store test file**

Run: `uv run pytest tests/mcp/test_config_store.py -v`
Expected: ALL PASS (17 tests).

- [ ] **Step 5: Commit**

```bash
git add src/octave/mcp/config_store.py tests/mcp/test_config_store.py
git commit -m "feat(mcp): McpConfigStore update/delete/list/set_enabled"
```

---

### Task 4: Public API exports

**Files:**
- Modify: `src/octave/mcp/__init__.py`
- Test: `tests/mcp/test_package.py`

- [ ] **Step 1: Write the failing test**

In `tests/mcp/test_package.py`, extend the name tuple in `test_public_names_are_exported` — insert after `"McpConfigError",`:

```python
        "McpServerNameTaken",
        "McpConfigStore",
        "McpServerEntry",
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/mcp/test_package.py -v`
Expected: FAIL — `assert hasattr(mcp_pkg, 'McpServerNameTaken')` AssertionError.

- [ ] **Step 3: Add the exports**

In `src/octave/mcp/__init__.py`: after the `from octave.mcp.config import ...` line add

```python
from octave.mcp.config_store import McpConfigStore, McpServerEntry
```

extend the `from octave.mcp.errors import (...)` block with `McpServerNameTaken`, and add to `__all__`:

```python
    "McpConfigStore",
    "McpServerEntry",
    "McpServerNameTaken",
```

(Keep `__all__` alphabetized as-is: `McpConfigStore` before `McpConfigError`? No — the list sorts `McpClient, McpConfigError, ...` case-insensitively; insert `"McpConfigStore"` right after `"McpConfigError"`, `"McpServerEntry"` after `"McpServerManager"`, `"McpServerNameTaken"` right after `"McpServerEntry"`.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/mcp/test_package.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add src/octave/mcp/__init__.py tests/mcp/test_package.py
git commit -m "feat(mcp): export McpConfigStore, McpServerEntry, McpServerNameTaken"
```

---

### Task 5: `mcp_lifespan` loads configs from the DB

**Files:**
- Modify: `src/octave/mcp/lifespan.py`
- Test: `tests/mcp/test_lifespan.py` (append)

**Design recap:** `configs: Iterable[...] | None = None`. Explicit iterable (including `()`) = register as today, DB untouched. `None` = read enabled rows via `app.state.db_session_factory`; missing factory → WARNING + zero servers; corrupt row → ERROR log + skip, siblings still boot. `app.py` untouched (`db_lifespan` composes first).

- [ ] **Step 1: Write the failing tests**

Append to `tests/mcp/test_lifespan.py` (add these imports at the top of the file):

```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from octave.db._bootstrap import create_sqlite_engine
from octave.db.models import Base
from octave.mcp.config import HttpConfig, StdioConfig
from octave.mcp.config_store import McpConfigStore
```

Then the tests (self-contained engine created inside the lifespan so all async work runs on the TestClient's loop — do NOT use the shared `engine` fixture with `TestClient`, it binds to the pytest-asyncio loop):

```python
# -- DB-backed boot (configs=None default) --------------------------------------


def _db_lifespan(
    db_url: str, fake: FakeFactory, seed
):  # noqa: ANN202 - test helper
    """Lifespan that builds a migrated SQLite, seeds it, then boots mcp_lifespan."""

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_sqlite_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await seed(McpConfigStore(session))
            await session.commit()
        app.state.db_session_factory = factory
        async with mcp_lifespan(app, client_factory=fake):
            yield
        await engine.dispose()

    return _lifespan


async def _seed_enabled_and_disabled(store: McpConfigStore) -> None:
    await store.create(name="Alpha", config=StdioConfig(command="alpha"))
    await store.create(
        name="Ghost", config=StdioConfig(command="ghost"), enabled=False
    )


def test_db_boot_registers_enabled_rows_only(tmp_path) -> None:  # noqa: ANN001
    fake = FakeFactory()
    app = FastAPI(
        lifespan=_db_lifespan(f"sqlite:///{tmp_path / 'boot.db'}", fake, _seed_enabled_and_disabled)
    )
    with TestClient(app) as client:
        manager: McpServerManager = client.app.state.mcp_manager
        assert [s.name for s in manager.status()] == ["Alpha"]
        assert manager.status()[0].state == "connected"


async def _seed_with_corrupt_row(store: McpConfigStore) -> None:
    """One good row + one CHECK-violating row (PRAGMA bypasses the constraint
    so the defensive per-row skip path is exercised)."""
    await store.create(name="Good", config=HttpConfig(url="http://h:1/m"))
    await store._session.exec_driver_sql(  # noqa: SLF001 - test needs raw escape hatch
        "PRAGMA ignore_check_constraints=ON"
    )
    await store._session.execute(  # noqa: SLF001
        text(
            "INSERT INTO mcp_servers (id, name, transport, command, url, enabled, "
            "created_at, updated_at) VALUES ('bad1', 'Broken', 'carrier-pigeon', "
            "NULL, NULL, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        ),
    )
    await store._session.exec_driver_sql(  # noqa: SLF001
        "PRAGMA ignore_check_constraints=OFF"
    )


def test_db_boot_skips_corrupt_row_and_boots_siblings(
    tmp_path, caplog
) -> None:  # noqa: ANN001
    import logging

    fake = FakeFactory()
    app = FastAPI(
        lifespan=_db_lifespan(
            f"sqlite:///{tmp_path / 'corrupt.db'}", fake, _seed_with_corrupt_row
        )
    )
    with caplog.at_level(logging.ERROR, logger="octave.mcp.lifespan"):
        with TestClient(app) as client:
            manager: McpServerManager = client.app.state.mcp_manager
            assert [s.name for s in manager.status()] == ["Good"]
    assert any("bad1" in rec.message or "Broken" in rec.message for rec in caplog.records)


def test_explicit_configs_bypass_db(tmp_path) -> None:  # noqa: ANN001
    """A passed configs iterable wins even when a session factory exists."""
    fake = FakeFactory()
    db_url = f"sqlite:///{tmp_path / 'bypass.db'}"

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_sqlite_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await McpConfigStore(session).create(
                name="FromDb", config=StdioConfig(command="db-server")
            )
            await session.commit()
        app.state.db_session_factory = factory
        configs = (("s1", "Explicit", StdioConfig(command="fake")),)
        async with mcp_lifespan(app, configs=configs, client_factory=fake):
            yield
        await engine.dispose()

    app = FastAPI(lifespan=_lifespan)
    with TestClient(app) as client:
        manager: McpServerManager = client.app.state.mcp_manager
        assert [s.name for s in manager.status()] == ["Explicit"]
```

Note: `test_default_configs_boots_empty` (existing, line 40) covers "no factory + `configs=None` → zero servers"; after this task it also logs a WARNING — its assertion (`status() == []`) must keep passing unchanged.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/mcp/test_lifespan.py -v`
Expected: the three new tests FAIL — `test_db_boot_registers_enabled_rows_only` gets `status() == []` (lifespan ignores the DB today); corrupt-row test fails on missing ERROR log; bypass test passes already (regression guard).

- [ ] **Step 3: Implement the DB read**

Replace the body of `mcp_lifespan` in `src/octave/mcp/lifespan.py` and add the helper. Full new file content:

```python
"""App lifespan for the MCP fleet: build → register → start → publish.

Startup posture (spec decision 6): a server that can't start does NOT abort
app boot — its supervisor enters the backoff/crashed path with ERROR logs.
Deliberate divergence from db_lifespan's fail-fast: MCP servers are
peripheral; the schema is not. The same posture extends to config reads
(issue #21): a corrupt row is logged ERROR and skipped; a missing
db_session_factory warns and boots zero servers.

``configs=None`` (the default) loads enabled rows from the unified
database via ``app.state.db_session_factory`` — db_lifespan composes first.
An explicit iterable (including empty) bypasses the DB entirely.

``start_all``/``stop_all`` run in the lifespan task, satisfying the manager's
same-task requirement for manual task-group entry.
"""

import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from octave.mcp.config import ServerConfig
from octave.mcp.config_store import McpConfigStore, to_server_config
from octave.mcp.errors import McpConfigError
from octave.mcp.manager import ClientFactory, McpServerManager
from octave.mcp.registry import ToolRegistry

__all__ = ["mcp_lifespan"]

logger = logging.getLogger(__name__)


async def _entries_from_db(app: FastAPI) -> list[tuple[str, str, ServerConfig]]:
    """Enabled server rows, converted per row so corrupt rows skip individually.

    Peripheral posture: never raises. Missing factory → WARNING + empty.
    Bad row → ERROR (id + reason; env/header values never appear) + skip.
    """
    factory = getattr(app.state, "db_session_factory", None)
    if factory is None:
        logger.warning(
            "no db_session_factory on app.state: booting with zero MCP servers"
        )
        return []
    entries: list[tuple[str, str, ServerConfig]] = []
    async with factory() as session:
        for row in await McpConfigStore(session).list_rows(enabled_only=True):
            try:
                config = to_server_config(row)
            except McpConfigError as exc:
                logger.error("skipping corrupt mcp_servers row %s: %s", row.id, exc)
                continue
            entries.append((row.id, row.name, config))
    return entries


@asynccontextmanager
async def mcp_lifespan(
    app: FastAPI,
    *,
    configs: Iterable[tuple[str, str, ServerConfig]] | None = None,
    client_factory: ClientFactory | None = None,
) -> AsyncIterator[None]:
    """Manage the MCP fleet for the app's lifetime.

    ``configs`` yields ``(id, name, config)`` triples. ``None`` (the
    default) loads enabled servers from the database (#21); an explicit
    iterable — including empty — is used verbatim.
    """
    entries = list(configs) if configs is not None else await _entries_from_db(app)
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

- [ ] **Step 4: Run the lifespan tests**

Run: `uv run pytest tests/mcp/test_lifespan.py -v`
Expected: ALL PASS (existing 7 + 3 new).

If the corrupt-row test fails because `PRAGMA ignore_check_constraints` is rejected inside the explicit-BEGIN transaction (aiosqlite threading note in tests/conftest.py), fall back to monkeypatching `octave.mcp.lifespan.to_server_config` to raise `McpConfigError` for `row.id == "bad1"` — same observable behavior, no pragma.

- [ ] **Step 5: Run the whole MCP + DB suites**

Run: `uv run pytest tests/mcp tests/db -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add src/octave/mcp/lifespan.py tests/mcp/test_lifespan.py
git commit -m "feat(mcp): load enabled server configs from DB at startup (issue #21)"
```

---

### Task 6: Docs check-off + full verification

**Files:**
- Modify: `docs/TODO.md:81`

- [ ] **Step 1: Check off MCP #7**

Replace line 81:

```markdown
- [ ] 7. Add MCP server configuration persistence (store server configs in the unified database)
```

with:

```markdown
- [x] 7. Add MCP server configuration persistence (store server configs in the unified database) — PR #144 (`McpConfigStore` + headers column + DB-backed `mcp_lifespan` boot)
```

- [ ] **Step 2: Full backend verification**

Run: `uv run pytest tests/ -v && uv run ruff check src/ tests/ && uv run mypy src/`
Expected: all green. Fix any lint/type findings inline (common: unused imports in lifespan, `Sequence` vs `list` return annotations).

- [ ] **Step 3: Commit**

```bash
git add docs/TODO.md
git commit -m "docs: check off MCP config persistence (issue #21, TODO MCP #7)"
```

- [ ] **Step 4: Push and mark PR ready for review**

```bash
git push
```

Then move draft PR #144 out of draft once CI is green.

---

## Self-Review (completed at plan authoring)

1. **Spec coverage:** headers column (Task 1) ✓; store CRUD surface create/update/delete/get/list_servers/set_enabled (Tasks 2–3) ✓; `McpServerNameTaken` + `McpConfigError` semantics (Task 2) ✓; never-commits contract test (Task 3) ✓; lifespan `configs=None` → DB, factory-missing WARNING, corrupt-row skip, explicit bypass (Task 5) ✓; `app.py` untouched (no task touches it) ✓; migration equivalence (Task 1, existing test) ✓; TODO #7 check-off (Task 6) ✓. Plan-time deviation from spec §2 (converter public + `list_rows`) documented in the header.
2. **Placeholder scan:** no TBD/TODO steps; every code step shows full code; the one known-fragile step (pragma in Task 5) carries a concrete fallback.
3. **Type consistency:** `McpServerEntry(id, name, enabled, config)`, `to_server_config(row)`, `list_rows(*, enabled_only)`, `list_servers(*, enabled_only)`, `create(*, name, config, enabled=True)`, `update(server_id, *, name, config, enabled)`, `set_enabled(server_id, enabled)` — signatures identical across Tasks 2, 3, 5 and the spec.
