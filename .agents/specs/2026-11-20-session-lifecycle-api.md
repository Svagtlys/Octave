# Session Lifecycle API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `POST /api/sessions` (create Session + owner membership, 201) and `GET /api/sessions` (caller's sessions, most-recently-active first, paginated, with last-message preview) per the approved design.

**Architecture:** A plane-owned `SessionStore` in `octave/db/session_store.py` (never commits — the `EventStore`/`VaultStore` contract) answers both queries with correlated scalar subqueries: `last_activity = COALESCE(MAX(events.created_at), sessions.created_at)` and `last_message` = content of the latest `user_message`/`assistant_message` event. Routes in `octave/routes/sessions.py` follow the auth-routes pattern exactly: `get_current_user` rides `get_db_session`, routes own commits, domain errors map to JSON once via `octave/middleware.py` (new `DbError` → 500 handler).

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async ORM, Pydantic v2, pytest + httpx ASGI transport on real SQLite files, uv.

**Spec:** [`.agents/specs/2026-11-20-session-lifecycle-api-design.md`](./2026-11-20-session-lifecycle-api-design.md) · **Issue:** #121 · **Branch:** `feature/session-create-list-api` · **PR:** https://github.com/Svagtlys/Octave/pull/147

**Gates (run from `backend/`):** `uv run pytest -q && uv run ruff check src tests && uv run mypy src` — all must pass before the final commit.

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `backend/src/octave/db/errors.py` | Modify | Add `OwnerParticipantMissingError(DbError)` |
| `backend/src/octave/middleware.py` | Modify | Add `DbError` → 500 JSON handler (single map point) |
| `backend/src/octave/db/session_store.py` | Create | `SessionStore` (create + membership-scoped activity listing), `SessionActivityRow` |
| `backend/src/octave/db/__init__.py` | Modify | Export `SessionStore`, `SessionActivityRow`, `OwnerParticipantMissingError` |
| `backend/src/octave/routes/sessions.py` | Create | `/sessions` router: POST 201 + GET paginated envelope, preview truncation |
| `backend/src/octave/app.py` | Modify | Mount sessions router under `/api` |
| `backend/tests/test_error_handling.py` | Modify | `DbError` → 500 mapping test |
| `backend/tests/db/test_session_store.py` | Create | Store tests on real SQLite (shared `session_factory` fixture) |
| `backend/tests/routes/test_sessions.py` | Create | Route tests on real SQLite (app assembled like `test_auth_routes.py`) |
| `.agents/memory/decisions.md` | Modify | ADR: membership-scoped listing, transcript-derived activity, fail-loud invariant |

Conventions to honor (read before starting): stores never commit (routes commit); TEXT columns + app-validated enums (`EventKind` is the source of truth for message kinds); tests run on temp-file SQLite through the shared `engine`/`session_factory` fixtures in `backend/tests/conftest.py` — no mocks; commit messages `type(scope): description`; mypy strict.

---

### Task 1: `OwnerParticipantMissingError` + middleware `DbError` handler

**Files:**
- Modify: `backend/src/octave/db/errors.py`
- Modify: `backend/src/octave/middleware.py`
- Test: `backend/tests/test_error_handling.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_error_handling.py` (and add the top-level imports shown below — the file currently imports only `pytest`, `ASGITransport`, `AsyncClient`):

```python
from fastapi import FastAPI

from octave.db.errors import OwnerParticipantMissingError
from octave.middleware import add_error_handlers


@pytest.mark.asyncio
async def test_db_error_maps_to_500_json() -> None:
    """DbError is the db-plane's single map point: invariant failures -> 500
    JSON with the error name (issue #121)."""
    app = FastAPI()
    add_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise OwnerParticipantMissingError("u_ghost")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    data = response.json()
    assert data["error"] == "OwnerParticipantMissingError"
    assert "u_ghost" in data["detail"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_error_handling.py -v`
Expected: FAIL — `ImportError: cannot import name 'OwnerParticipantMissingError' from 'octave.db.errors'`

- [ ] **Step 3: Implement the error class**

In `backend/src/octave/db/errors.py`, add `"OwnerParticipantMissingError"` to `__all__` (alphabetical, after `"UnknownDbAdapterError"` is fine but keep the list sorted — insert between `"DbMigrationError"` and `"UnknownDbAdapterError"` per the existing alphabetical order), and append the class:

```python
class OwnerParticipantMissingError(DbError):
    """A user has no Participant row — the invariant is broken.

    ``AuthStore.create_user`` and the auth backfill migration guarantee one
    Participant per user; a missing row is a data-integrity bug. Fail loud;
    never self-heal by inserting a Participant on the fly (issue #121).
    """

    def __init__(self, user_id: str) -> None:
        super().__init__(f"user {user_id!r} has no participant row")
        self.user_id = user_id
```

- [ ] **Step 4: Implement the middleware handler**

In `backend/src/octave/middleware.py`, add the import after the existing `from octave.auth.errors import AuthError, http_status_of` line:

```python
from octave.db.errors import DbError
```

and inside `add_error_handlers`, after the `auth_error` handler, add:

```python
    @app.exception_handler(DbError)
    async def db_error(request: Request, exc: DbError) -> JSONResponse:
        """Single map point: db-plane invariant failures (e.g.
        OwnerParticipantMissingError) -> 500 JSON. These are data-integrity
        bugs, not client errors — the error name is the diagnostic."""
        return JSONResponse(
            status_code=500,
            content={"error": type(exc).__name__, "detail": str(exc)},
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_error_handling.py -v`
Expected: PASS (2 tests, including the pre-existing 404 test)

- [ ] **Step 6: Commit**

```bash
git add backend/src/octave/db/errors.py backend/src/octave/middleware.py backend/tests/test_error_handling.py
git commit -m "feat(db): add OwnerParticipantMissingError and DbError -> 500 middleware mapping"
```

---

### Task 2: `SessionStore` — participant lookup + `create_session`

**Files:**
- Create: `backend/src/octave/db/session_store.py`
- Modify: `backend/src/octave/db/__init__.py`
- Test: `backend/tests/db/test_session_store.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/db/test_session_store.py` (uses the shared `session_factory` fixture from `backend/tests/conftest.py`; timestamps explicit for deterministic ordering):

```python
"""SessionStore tests on real SQLite (issue #121).

Shared engine/session_factory fixtures: temp-file SQLite, constraints live.
Timestamps are set explicitly so ordering never depends on wall-clock
resolution. Events are appended through EventStore (the canonical write path),
then re-timed for determinism.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.db.event_store import EventStore
from octave.db.models import Event, Participant, Session, SessionParticipant, User
from octave.db.session_store import SessionStore
from octave.db.types import EventKind


def _dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=timezone.utc)


async def _seed_user(
    session: AsyncSession, *, user_id: str, participant_id: str, display_name: str
) -> None:
    session.add(User(id=user_id, username=user_id, display_name=display_name))
    session.add(Participant(id=participant_id, user_id=user_id, label=display_name))
    await session.flush()


async def _make_session(
    session: AsyncSession,
    *,
    session_id: str,
    owner_user_id: str,
    created_at: datetime,
    title: str | None = None,
) -> Session:
    row = Session(
        id=session_id,
        created_by_user_id=owner_user_id,
        status="active",
        title=title,
        created_at=created_at,
    )
    session.add(row)
    await session.flush()
    return row


async def _append(
    session: AsyncSession,
    session_id: str,
    kind: EventKind,
    content: str,
    *,
    author: str | None = None,
    created_at: datetime | None = None,
) -> Event:
    event = await EventStore(session).append(
        session_id, kind, author_participant_id=author, payload={"content": content}
    )
    if created_at is not None:
        event.created_at = created_at
        await session.flush()
    return event


# -- participant lookup ---------------------------------------------------------


async def test_get_participant_for_user_hit_and_miss(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        store = SessionStore(session)
        found = await store.get_participant_for_user("u_1")
        assert found is not None
        assert found.id == "p_1"
        assert await store.get_participant_for_user("u_ghost") is None


# -- create_session ---------------------------------------------------------------


async def test_create_session_persists_session_and_owner_membership(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        row = await SessionStore(session).create_session(
            owner_user_id="u_1", owner_participant_id="p_1", title="Planning"
        )
        await session.commit()
        session_id = row.id
        assert len(session_id) == 32  # uuid4 hex

    async with session_factory() as check:
        loaded = await check.get(Session, session_id)
        assert loaded is not None
        assert loaded.status == "active"
        assert loaded.title == "Planning"
        assert loaded.created_by_user_id == "u_1"
        membership = (
            await check.execute(select(SessionParticipant))
        ).scalars().one()
        assert membership.session_id == session_id
        assert membership.participant_id == "p_1"
        assert membership.role == "speaker"
        assert membership.left_at is None


async def test_create_session_title_optional(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        row = await SessionStore(session).create_session(
            owner_user_id="u_1", owner_participant_id="p_1", title=None
        )
        await session.commit()
        session_id = row.id
    async with session_factory() as check:
        loaded = await check.get(Session, session_id)
        assert loaded is not None
        assert loaded.title is None


async def test_create_session_never_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An uncommitted create is invisible to a second session (store contract)."""
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        await SessionStore(session).create_session(
            owner_user_id="u_1", owner_participant_id="p_1", title="Quiet"
        )
        # no commit
    async with session_factory() as check:
        count = await check.scalar(select(func.count()).select_from(Session))
        assert count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/db/test_session_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octave.db.session_store'`

- [ ] **Step 3: Write the store (create + lookup only for now)**

Create `backend/src/octave/db/session_store.py`:

```python
"""Session lifecycle queries: create + membership-scoped activity listing.

Plane-owned store over one caller-supplied ``AsyncSession`` — same contract as
``EventStore``/``VaultStore``/``McpConfigStore``: **never commits**; routes own
transaction boundaries (``octave.db.deps.get_db_session``).

Activity and preview are transcript-derived (2026-11-20 design spec):
``last_activity`` is ``COALESCE(MAX(events.created_at), sessions.created_at)``
and ``last_message`` the content of the latest ``user_message`` /
``assistant_message`` event (other kinds never produce a preview). Correlated
scalar subqueries keep ``ORDER BY``/``LIMIT``/``OFFSET`` a single statement
without ``GROUP BY``; the preview subquery rides the unique index
``(session_id, seq)``.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.db.models import Event, Participant, Session, SessionParticipant
from octave.db.types import EventKind

__all__ = ["SessionActivityRow", "SessionStore"]

_MESSAGE_KINDS = (str(EventKind.USER_MESSAGE), str(EventKind.ASSISTANT_MESSAGE))


@dataclass(frozen=True)
class SessionActivityRow:
    """One listed session with transcript-derived activity (raw, untruncated)."""

    id: str
    title: str | None
    created_at: datetime
    last_activity: datetime
    last_message: str | None


class SessionStore:
    """Session create/list queries. Never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_participant_for_user(self, user_id: str) -> Participant | None:
        """The user's speaker identity. ``None`` means the create_user
        invariant is broken — callers fail loud, never self-heal."""
        result = await self._session.execute(
            select(Participant).where(Participant.user_id == user_id)
        )
        return result.scalars().first()

    async def create_session(
        self, *, owner_user_id: str, owner_participant_id: str, title: str | None
    ) -> Session:
        """Insert the Session (status ``active``, uuid4-hex id) and the owner's
        membership row (role ``speaker``) in one flush. Never commits."""
        row = Session(
            id=uuid.uuid4().hex,
            created_by_user_id=owner_user_id,
            status="active",
            title=title,
        )
        self._session.add(row)
        self._session.add(
            SessionParticipant(
                session_id=row.id,
                participant_id=owner_participant_id,
                role="speaker",
            )
        )
        await self._session.flush()
        return row
```

In `backend/src/octave/db/__init__.py`: extend the errors import to
`from octave.db.errors import DbError, OwnerParticipantMissingError`, add
`from octave.db.session_store import SessionActivityRow, SessionStore` (after the
`registry` import, alphabetical), and add `"OwnerParticipantMissingError"`,
`"SessionActivityRow"`, `"SessionStore"` to `__all__` in alphabetical position.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/db/test_session_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/db/session_store.py backend/src/octave/db/__init__.py backend/tests/db/test_session_store.py
git commit -m "feat(db): SessionStore create + participant lookup with owner membership"
```

---

### Task 3: `SessionStore.list_sessions` — ordering, preview, pagination

**Files:**
- Modify: `backend/src/octave/db/session_store.py`
- Test: `backend/tests/db/test_session_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/db/test_session_store.py`:

```python
# -- list_sessions: ordering ------------------------------------------------------


async def test_orders_by_latest_event_with_created_at_fallback(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Sessions with events sort by MAX(events.created_at); eventless sessions
    fall back to sessions.created_at. Mixed list interleaves."""
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        for sid, day in [("s_a", 1), ("s_b", 2), ("s_c", 3)]:
            await _make_session(
                session, session_id=sid, owner_user_id="u_1", created_at=_dt(day)
            )
            await session.flush()
            member = SessionParticipant(session_id=sid, participant_id="p_1")
            session.add(member)
        await _append(
            session, "s_c", EventKind.USER_MESSAGE, "late", created_at=_dt(10)
        )
        await session.commit()

    async with session_factory() as session:
        rows, total = await SessionStore(session).list_sessions(
            participant_id="p_1", limit=10, offset=0
        )
        assert [r.id for r in rows] == ["s_c", "s_b", "s_a"]
        assert total == 3


async def test_tie_break_is_id_desc(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        for sid in ("s_a", "s_b"):
            await _make_session(
                session, session_id=sid, owner_user_id="u_1", created_at=_dt(5)
            )
            session.add(SessionParticipant(session_id=sid, participant_id="p_1"))
        await session.commit()

    async with session_factory() as session:
        rows, _ = await SessionStore(session).list_sessions(
            participant_id="p_1", limit=10, offset=0
        )
        assert [r.id for r in rows] == ["s_b", "s_a"]


# -- list_sessions: preview ---------------------------------------------------------


async def test_preview_picks_latest_message_ignoring_other_kinds(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Latest user/assistant message by seq wins; tool_call never previews but
    still counts for last_activity."""
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        session.add(SessionParticipant(session_id="s_1", participant_id="p_1"))
        await _append(
            session, "s_1", EventKind.USER_MESSAGE, "first", author="p_1", created_at=_dt(5)
        )
        await _append(
            session, "s_1", EventKind.ASSISTANT_MESSAGE, "second", created_at=_dt(6)
        )
        await _append(session, "s_1", EventKind.TOOL_CALL, "tool", created_at=_dt(7))
        await session.commit()

    async with session_factory() as session:
        rows, _ = await SessionStore(session).list_sessions(
            participant_id="p_1", limit=10, offset=0
        )
        assert rows[0].last_message == "second"
        assert rows[0].last_activity == _dt(7)


async def test_preview_null_without_message_events(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A session with only system events has activity but no preview."""
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        session.add(SessionParticipant(session_id="s_1", participant_id="p_1"))
        await _append(
            session, "s_1", EventKind.SYSTEM, "notice", created_at=_dt(4)
        )
        await session.commit()

    async with session_factory() as session:
        rows, _ = await SessionStore(session).list_sessions(
            participant_id="p_1", limit=10, offset=0
        )
        assert rows[0].last_message is None
        assert rows[0].last_activity == _dt(4)


# -- list_sessions: scoping + pagination ----------------------------------------


async def test_scoping_excludes_other_users_sessions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        await _seed_user(session, user_id="u_2", participant_id="p_2", display_name="Bob")
        await _make_session(
            session, session_id="s_alice", owner_user_id="u_1", created_at=_dt(1)
        )
        session.add(SessionParticipant(session_id="s_alice", participant_id="p_1"))
        await _make_session(
            session, session_id="s_bob", owner_user_id="u_2", created_at=_dt(2)
        )
        session.add(SessionParticipant(session_id="s_bob", participant_id="p_2"))
        await session.commit()

    async with session_factory() as session:
        rows, total = await SessionStore(session).list_sessions(
            participant_id="p_1", limit=10, offset=0
        )
        assert [r.id for r in rows] == ["s_alice"]
        assert total == 1


async def test_left_membership_excluded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        session.add(
            SessionParticipant(
                session_id="s_1", participant_id="p_1", left_at=_dt(3)
            )
        )
        await session.commit()

    async with session_factory() as session:
        rows, total = await SessionStore(session).list_sessions(
            participant_id="p_1", limit=10, offset=0
        )
        assert rows == []
        assert total == 0


async def test_pagination_slices_and_total(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """total counts all current memberships regardless of page slice; offset
    past the end returns an empty page with the real total."""
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        for sid, day in [("s_1", 1), ("s_2", 2), ("s_3", 3)]:
            await _make_session(
                session, session_id=sid, owner_user_id="u_1", created_at=_dt(day)
            )
            session.add(SessionParticipant(session_id=sid, participant_id="p_1"))
        await session.commit()

    async with session_factory() as session:
        store = SessionStore(session)
        page1, total1 = await store.list_sessions(
            participant_id="p_1", limit=2, offset=0
        )
        assert [r.id for r in page1] == ["s_3", "s_2"]
        assert total1 == 3
        page2, total2 = await store.list_sessions(
            participant_id="p_1", limit=2, offset=2
        )
        assert [r.id for r in page2] == ["s_1"]
        assert total2 == 3
        beyond, total3 = await store.list_sessions(
            participant_id="p_1", limit=2, offset=5
        )
        assert beyond == []
        assert total3 == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/db/test_session_store.py -v`
Expected: FAIL — `AttributeError: 'SessionStore' object has no attribute 'list_sessions'` (the 4 Task-2 tests still pass)

- [ ] **Step 3: Implement `list_sessions`**

Append to `class SessionStore` in `backend/src/octave/db/session_store.py`:

```python
    async def list_sessions(
        self, *, participant_id: str, limit: int, offset: int
    ) -> tuple[list[SessionActivityRow], int]:
        """Current memberships for ``participant_id``, most-recently-active
        first (``COALESCE(MAX(events.created_at), created_at)``, tie-break id
        DESC), plus the caller's total membership count. Never commits."""
        last_activity = func.coalesce(
            select(func.max(Event.created_at))
            .where(Event.session_id == Session.id)
            .correlate(Session)
            .scalar_subquery(),
            Session.created_at,
        ).label("last_activity")
        last_message_payload = (
            select(Event.payload)
            .where(
                Event.session_id == Session.id,
                Event.kind.in_(_MESSAGE_KINDS),
            )
            .order_by(Event.seq.desc())
            .limit(1)
            .correlate(Session)
            .scalar_subquery()
            .label("last_message_payload")
        )
        membership = select(SessionParticipant.session_id).where(
            SessionParticipant.participant_id == participant_id,
            SessionParticipant.left_at.is_(None),
        )
        rows = await self._session.execute(
            select(
                Session.id,
                Session.title,
                Session.created_at,
                last_activity,
                last_message_payload,
            )
            .where(Session.id.in_(membership))
            .order_by(last_activity.desc(), Session.id.desc())
            .limit(limit)
            .offset(offset)
        )
        total = await self._session.scalar(
            select(func.count())
            .select_from(SessionParticipant)
            .where(
                SessionParticipant.participant_id == participant_id,
                SessionParticipant.left_at.is_(None),
            )
        )
        items: list[SessionActivityRow] = []
        for row in rows:
            raw: Any = row.last_message_payload
            content = raw.get("content") if isinstance(raw, dict) else None
            items.append(
                SessionActivityRow(
                    id=row.id,
                    title=row.title,
                    created_at=row.created_at,
                    last_activity=row.last_activity,
                    last_message=content,
                )
            )
        return items, int(total or 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/db/test_session_store.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Run the db-plane gates**

Run: `cd backend && uv run ruff check src tests && uv run mypy src`
Expected: no errors (mypy strict clean)

- [ ] **Step 6: Commit**

```bash
git add backend/src/octave/db/session_store.py backend/tests/db/test_session_store.py
git commit -m "feat(db): SessionStore.list_sessions with transcript-derived activity and preview"
```

---

### Task 4: Routes — `POST /api/sessions`

**Files:**
- Create: `backend/src/octave/routes/sessions.py`
- Modify: `backend/src/octave/app.py`
- Test: `backend/tests/routes/test_sessions.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/routes/test_sessions.py` (app assembled exactly like
`test_auth_routes.py`: temp-file SQLite, `DummyHasher`, `add_error_handlers`):

```python
"""Session routes over the real app surface (issue #121).

App assembled like tests/routes/test_auth_routes.py: temp-file SQLite,
DummyHasher, auth + sessions routers, add_error_handlers. Events seeded via
EventStore, then re-timed for deterministic ordering.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.auth.config import AuthSettings
from octave.auth.deps import SESSION_COOKIE
from octave.auth.passwords import DummyHasher
from octave.db._bootstrap import create_sqlite_engine
from octave.db.event_store import EventStore
from octave.db.models import Base, Participant, Session, SessionParticipant
from octave.db.types import EventKind
from octave.middleware import add_error_handlers
from octave.routes.auth import router as auth_router
from octave.routes.sessions import router as sessions_router


def _dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=timezone.utc)


async def _sessions_app(
    tmp_path: Path,
) -> tuple[FastAPI, async_sessionmaker[AsyncSession]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'routes.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.state.db_session_factory = factory
    app.state.auth_hasher = DummyHasher()
    app.state.auth_settings = AuthSettings(cookie_secure=False)
    add_error_handlers(app)
    app.include_router(auth_router, prefix="/api")
    app.include_router(sessions_router, prefix="/api")
    return app, factory


@pytest.fixture
async def app_and_factory(tmp_path: Path):
    return await _sessions_app(tmp_path)


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _bootstrap(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/bootstrap",
        json={"username": "alice", "password": "pw", "display_name": "Alice"},
    )
    assert response.status_code == 201


async def _participant_id(factory, user_id: str) -> str:
    async with factory() as session:
        return (
            await session.execute(
                select(Participant.id).where(Participant.user_id == user_id)
            )
        ).scalar_one()


async def _seed_activity(
    factory,
    session_id: str,
    events: list[tuple[EventKind, str, datetime]],
    *,
    author: str | None = None,
) -> None:
    async with factory() as session:
        for kind, content, when in events:
            event = await EventStore(session).append(
                session_id, kind, author_participant_id=author,
                payload={"content": content},
            )
            event.created_at = when
        await session.commit()


async def _set_session_created_at(
    factory, session_id: str, when: datetime
) -> None:
    async with factory() as session:
        row = await session.get(Session, session_id)
        assert row is not None
        row.created_at = when
        await session.commit()


# -- create ------------------------------------------------------------------


async def test_create_requires_auth(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        assert (await client.post("/api/sessions", json={})).status_code == 401
        assert (await client.get("/api/sessions")).status_code == 401


async def test_create_returns_201_and_persists(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        me = (await client.get("/api/auth/me")).json()
        response = await client.post("/api/sessions", json={"title": "Groceries"})
        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "Groceries"
        assert body["last_message"] is None
        assert body["last_activity"] == body["created_at"]

    async with factory() as session:
        srow = await session.get(Session, body["id"])
        assert srow is not None
        assert srow.status == "active"
        assert srow.created_by_user_id == me["id"]
        membership = (
            await session.execute(select(SessionParticipant))
        ).scalars().one()
        assert membership.participant_id == await _participant_id(
            factory, me["id"]
        )
        assert membership.role == "speaker"
        assert membership.left_at is None


async def test_create_title_optional(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        response = await client.post("/api/sessions", json={})
        assert response.status_code == 201
        assert response.json()["title"] is None


async def test_create_rejects_oversized_title(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        response = await client.post(
            "/api/sessions", json={"title": "x" * 201}
        )
        assert response.status_code == 422


async def test_create_fails_loud_without_participant(app_and_factory) -> None:
    """Missing owner Participant is a data-integrity bug: 500, never a
    self-healed insert (design Decision 5)."""
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        me = (await client.get("/api/auth/me")).json()
    async with factory() as session:
        await session.execute(
            text("DELETE FROM participants WHERE user_id = :uid"),
            {"uid": me["id"]},
        )
        await session.commit()
    async with await _client(app) as client:
        await _bootstrap_cookie(client, me)
        response = await client.post("/api/sessions", json={})
        assert response.status_code == 500
        assert response.json()["error"] == "OwnerParticipantMissingError"


async def _bootstrap_cookie(client: AsyncClient, identity: dict) -> None:
    """Re-login the (still-existing) user after the participant row was
    deleted, so get_current_user resolves but the lookup fails."""
    response = await client.post(
        "/api/auth/login",
        json={"username": identity["username"], "password": "pw"},
    )
    assert response.status_code == 200
    assert SESSION_COOKIE in response.cookies
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/routes/test_sessions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octave.routes.sessions'`

- [ ] **Step 3: Implement the routes module (POST first)**

Create `backend/src/octave/routes/sessions.py`:

```python
"""Session lifecycle HTTP surface (issue #121).

POST creates a Session plus the owner's membership row in one transaction;
GET lists the caller's sessions, most-recently-active first, with a
last-message preview. Routes own commits (deps compose ``get_db_session``);
db-plane errors map to JSON once via ``octave.middleware`` (DbError -> 500).
The caller's missing Participant row fails loud — never self-healed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from octave.auth.deps import get_current_user
from octave.db.deps import get_db_session
from octave.db.errors import OwnerParticipantMissingError
from octave.db.models import User
from octave.db.session_store import SessionActivityRow, SessionStore

__all__ = ["router"]

router = APIRouter(prefix="/sessions", tags=["sessions"])

PREVIEW_MAX_CHARS = 140


class CreateSessionRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class SessionResponse(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    last_activity: datetime
    last_message: str | None


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    total: int
    limit: int
    offset: int


def _preview(content: str | None) -> str | None:
    """Truncate the preview at the route boundary (presentation concern; the
    store returns raw content). Total length stays <= PREVIEW_MAX_CHARS."""
    if content is None or len(content) <= PREVIEW_MAX_CHARS:
        return content
    return content[: PREVIEW_MAX_CHARS - 1] + "…"


async def _require_participant_id(
    store: SessionStore, user: User
) -> str:
    participant = await store.get_participant_for_user(user.id)
    if participant is None:
        raise OwnerParticipantMissingError(user.id)
    return participant.id


@router.post("", status_code=201)
async def create_session(
    body: CreateSessionRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_db_session),
) -> SessionResponse:
    """Create a session owned by the caller, with the caller's Participant
    as a speaker member. 201; fresh session: last_activity = created_at."""
    store = SessionStore(session)
    participant_id = await _require_participant_id(store, user)
    row = await store.create_session(
        owner_user_id=user.id,
        owner_participant_id=participant_id,
        title=body.title,
    )
    await session.commit()
    return SessionResponse(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        last_activity=row.created_at,
        last_message=None,
    )
```

In `backend/src/octave/app.py`: add `from octave.routes.sessions import router as sessions_router` (after the auth router import) and `app.include_router(sessions_router, prefix="/api")` (after the auth include line).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/routes/test_sessions.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/routes/sessions.py backend/src/octave/app.py backend/tests/routes/test_sessions.py
git commit -m "feat(routes): POST /api/sessions creates session with owner membership"
```

---

### Task 5: Routes — `GET /api/sessions`

**Files:**
- Modify: `backend/src/octave/routes/sessions.py`
- Test: `backend/tests/routes/test_sessions.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/routes/test_sessions.py`:

```python
# -- list ---------------------------------------------------------------------


async def test_list_scoped_to_caller(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as owner:
        await _bootstrap(owner)  # alice, owner role
        created = await owner.post("/api/auth/users", json={
            "username": "bob", "password": "pw", "display_name": "Bob",
        })
        assert created.status_code == 201
        await owner.post("/api/sessions", json={"title": "Alice only"})

        bob = await _client(app)
        await bob.post(
            "/api/auth/login", json={"username": "bob", "password": "pw"}
        )
        bob_list = await bob.get("/api/sessions")
        assert bob_list.status_code == 200
        assert bob_list.json()["items"] == []
        assert bob_list.json()["total"] == 0

        alice_list = await owner.get("/api/sessions")
        assert [item["title"] for item in alice_list.json()["items"]] == [
            "Alice only"
        ]
        assert alice_list.json()["total"] == 1


async def test_list_orders_most_recently_active_first(app_and_factory) -> None:
    """Event activity beats creation time; eventless sessions fall back to
    created_at."""
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        ids = {}
        for sid, day in [("s_a", 1), ("s_b", 2), ("s_c", 3)]:
            response = await client.post("/api/sessions", json={"title": sid})
            ids[sid] = response.json()["id"]
    for sid, day in [("s_a", 1), ("s_b", 2), ("s_c", 3)]:
        await _set_session_created_at(factory, ids[sid], _dt(day))
    await _seed_activity(
        factory, ids["s_a"], [(EventKind.USER_MESSAGE, "hi", _dt(10))]
    )
    async with await _client(app) as client:
        await _bootstrap_login(client)
        body = (await client.get("/api/sessions")).json()
        assert [item["id"] for item in body["items"]] == [
            ids["s_a"],
            ids["s_c"],
            ids["s_b"],
        ]


async def _bootstrap_login(client: AsyncClient) -> None:
    """Re-authenticate an existing client on a fresh app (alice persists in
    the temp DB)."""
    response = await client.post(
        "/api/auth/login", json={"username": "alice", "password": "pw"}
    )
    assert response.status_code == 200


async def test_preview_truncated_at_140(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        session_id = (await client.post("/api/sessions", json={})).json()["id"]
        alice = await _participant_id(factory, (await client.get("/api/auth/me")).json()["id"])
    await _seed_activity(
        factory,
        session_id,
        [(EventKind.USER_MESSAGE, "x" * 200, _dt(5))],
        author=alice,
    )
    async with await _client(app) as client:
        await _bootstrap_login(client)
        item = (await client.get("/api/sessions")).json()["items"][0]
        assert item["last_message"] == "x" * 139 + "…"
        assert len(item["last_message"]) == 140


async def test_preview_null_without_message_events(app_and_factory) -> None:
    """tool_call counts for activity but never produces a preview."""
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        session_id = (await client.post("/api/sessions", json={})).json()["id"]
    await _seed_activity(
        factory, session_id, [(EventKind.TOOL_CALL, "tool", _dt(7))]
    )
    async with await _client(app) as client:
        await _bootstrap_login(client)
        item = (await client.get("/api/sessions")).json()["items"][0]
        assert item["last_message"] is None
        assert datetime.fromisoformat(item["last_activity"]) == _dt(7)


async def test_list_pagination_envelope(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        ids = {}
        for sid, day in [("s_1", 1), ("s_2", 2), ("s_3", 3)]:
            response = await client.post("/api/sessions", json={"title": sid})
            ids[sid] = response.json()["id"]
    for sid, day in [("s_1", 1), ("s_2", 2), ("s_3", 3)]:
        await _set_session_created_at(factory, ids[sid], _dt(day))
    async with await _client(app) as client:
        await _bootstrap_login(client)
        defaults = (await client.get("/api/sessions")).json()
        assert defaults["limit"] == 20
        assert defaults["offset"] == 0
        assert defaults["total"] == 3

        page = (await client.get("/api/sessions?limit=2&offset=0")).json()
        assert [item["id"] for item in page["items"]] == [ids["s_3"], ids["s_2"]]
        assert page["total"] == 3

        beyond = (await client.get("/api/sessions?limit=2&offset=5")).json()
        assert beyond["items"] == []
        assert beyond["total"] == 3


async def test_pagination_validation(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        for url in (
            "/api/sessions?limit=0",
            "/api/sessions?limit=101",
            "/api/sessions?offset=-1",
        ):
            assert (await client.get(url)).status_code == 422, url
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/routes/test_sessions.py -v`
Expected: FAIL on the new list tests (404 for `GET /api/sessions`); Task-4 tests still pass

- [ ] **Step 3: Implement the GET endpoint**

Append to `backend/src/octave/routes/sessions.py`:

```python
def _to_response(row: SessionActivityRow) -> SessionResponse:
    return SessionResponse(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        last_activity=row.last_activity,
        last_message=_preview(row.last_message),
    )


@router.get("")
async def list_sessions(
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_db_session),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> SessionListResponse:
    """The caller's current memberships, most-recently-active first
    (transcript activity, fallback created_at), paginated."""
    store = SessionStore(session)
    participant_id = await _require_participant_id(store, user)
    rows, total = await store.list_sessions(
        participant_id=participant_id, limit=limit, offset=offset
    )
    return SessionListResponse(
        items=[_to_response(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
```

- [ ] **Step 4: Run the full route suite to verify it passes**

Run: `cd backend && uv run pytest tests/routes/test_sessions.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Run the auth guard regression**

Run: `cd backend && uv run pytest tests/routes/test_auth_routes.py -v`
Expected: PASS — including `test_every_non_public_route_requires_identity`, which now covers the mounted `/api/sessions` routes (both depend on `get_current_user`).

- [ ] **Step 6: Commit**

```bash
git add backend/src/octave/routes/sessions.py backend/tests/routes/test_sessions.py
git commit -m "feat(routes): GET /api/sessions paginated activity listing with preview"
```

---

### Task 6: Full gates + ADR

**Files:**
- Modify: `.agents/memory/decisions.md`

- [ ] **Step 1: Run the full gate suite**

Run from `backend/`:

```bash
uv run pytest -q && uv run ruff check src tests && uv run mypy src
```

Expected: all tests pass; ruff "All checks passed!"; mypy "Success: no issues found in N source files" (strict).

- [ ] **Step 2: Write the ADR**

Append to `.agents/memory/decisions.md` (end of the Decisions section):

```markdown
### 2026-11-20 — Session Lifecycle API: membership-scoped listing, transcript-derived activity

**Context:** Issue #121 ships the first user-facing session surface (POST create + GET list). Two axes needed decisions: the listing visibility axis (ownership vs membership) and the activity/preview source (transcript-derived vs denormalized column).

**Options Considered:** 1) ownership-scoped listing (`created_by_user_id`) + denormalized `last_activity` column; 2) membership-scoped listing (`session_participants`, `left_at IS NULL`) + transcript-derived activity/preview via correlated scalar subqueries; 3) LEFT JOIN/GROUP BY listing with a batch preview query.

**Decision:** Option 2. `SessionStore` (never-commits, plane-owned store contract) lists via the membership join; `last_activity = COALESCE(MAX(events.created_at), sessions.created_at)`; preview = content of the latest `user_message`/`assistant_message` event (140-char truncation at the route boundary). A missing owner Participant fails loud: `OwnerParticipantMissingError(DbError)` → new middleware `DbError` → 500 handler; never self-healed.

**Rationale:** Membership is the future-proof visibility axis — sharing a session later adds membership rows with zero query change. The transcript is the activity source of truth; a denormalized clock would drift and duplicate the events table's authority. Correlated subqueries avoid `GROUP BY` (no column enumeration) and ride the unique `(session_id, seq)` index; cost is per-row subquery execution, irrelevant at local-first single-instance scale. Self-healing the Participant invariant would paper over a backfill bug.

**Consequences:** The chat UI (UI Chat Interface #4) and Integration #1 get a stable session-list contract pinned by store tests. `DbError` → 500 is the second plane-level middleware mapping; future db-plane invariant errors ride the same handler. Listing sessions created by others but joined becomes possible the moment a sharing work item writes membership rows. `total` in the envelope assumes membership counts are small (personal scale); cursor pagination is reserved for if that ever breaks.
```

- [ ] **Step 3: Commit**

```bash
git add .agents/memory/decisions.md
git commit -m "docs(memory): ADR for session lifecycle API listing and activity decisions"
```

---

## Self-Review (completed by plan author)

1. **Spec coverage:** Design Decisions 1–7 each map to tasks — store seam (T2/T3), correlated subqueries + fallback (T3), membership scoping incl. `left_at` (T3), preview kinds/truncation/null (T3/T5), fail-loud error + middleware (T1/T4), envelope + session fields (T4/T5), pagination defaults/validation (T5). Test matrix from the spec is fully covered by T2–T5. No gaps.
2. **Placeholder scan:** No TBD/"similar to Task N"/vague steps; every code step is complete code; every command has expected output.
3. **Type consistency:** `SessionStore.get_participant_for_user`/`create_session(*, owner_user_id, owner_participant_id, title)`/`list_sessions(*, participant_id, limit, offset) -> tuple[list[SessionActivityRow], int]`, `SessionActivityRow(id, title, created_at, last_activity, last_message)`, `OwnerParticipantMissingError(user_id)`, `CreateSessionRequest`/`SessionResponse`/`SessionListResponse`, `PREVIEW_MAX_CHARS = 140` — identical across tasks and tests.
