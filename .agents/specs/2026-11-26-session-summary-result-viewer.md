# Session Summary Result Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Agent Manager result viewer: backend `GET/POST /api/session-summaries` (list with attached cached summaries, peek-detail, force-refresh via `SessionSummarizer`), and a master–detail `/agents` view on the frontend.

**Architecture:** Dedicated `/api/session-summaries` router (spec Decision 2) joining `SessionStore.list_sessions` to `VaultStore.list_items(kind=SESSION_SUMMARY)` for the list, `SessionSummarizer.peek`/`collect(force=True)` for detail/refresh, wired through a new `build_session_summarizer` composition helper reading `app.state.db_adapter` / `app.state.inference_adapter`. Frontend: `lib/api/summaries.ts` + `useSessionSummaries` hook + `AgentsView` replacing the `/agents` placeholder.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, pytest + httpx ASGI, uv; React 19, TanStack Router, Vitest + Testing Library.

**Spec:** [`.agents/specs/2026-11-26-session-summary-result-viewer-design.md`](2026-11-26-session-summary-result-viewer-design.md) — decisions 1–6 are approved; do not re-litigate them.

**Conventions you must follow (already in the codebase):**
- Routes never commit except where stated; `get_db_session` rolls back on exception.
- `SessionSummarizer` / `VaultStore` never commit — the refresh route owns the commit.
- Access gate: `SessionStore.resolve_access(session_id, user_id)` → `"missing"`→404, `"denied"`→403.
- Run backend from `backend/`: `uv run pytest ...`, `uv run ruff check src tests`.
- Run frontend from `frontend/`: `npx vitest run <path>`, `npx tsc -b`, `npm run lint`.
- You are on branch `feature/session-summary-result-viewer` (PR #165). Commit per task.

**File map (final state):**

| File | Responsibility |
|---|---|
| `backend/src/octave/agent/composition.py` | + `build_session_summarizer` wiring helper |
| `backend/src/octave/routes/summaries.py` | NEW — the three endpoints + response models |
| `backend/src/octave/app.py` | Mount `summaries_router` |
| `backend/tests/agent/test_composition.py` | + helper tests |
| `backend/tests/routes/test_summaries.py` | NEW — route tests |
| `frontend/src/lib/api/summaries.ts` | NEW — typed API calls |
| `frontend/src/lib/api/summaries.test.ts` | NEW — client contract |
| `frontend/src/lib/api/useSessionSummaries.ts` | NEW — list hook |
| `frontend/src/lib/api/useSessionSummaries.test.ts` | NEW — hook tests |
| `frontend/src/views/AgentsView.tsx` + `.module.css` | NEW — master–detail viewer |
| `frontend/tests/views/AgentsView.test.tsx` | NEW — view tests |
| `frontend/src/router.tsx` | Swap placeholder → `AgentsView` |
| `frontend/tests/App.test.tsx` | Update the `/agents` route test |

---

### Task 1: `build_session_summarizer` composition helper

**Files:**
- Modify: `backend/src/octave/agent/composition.py`
- Test: `backend/tests/agent/test_composition.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_composition.py`. The vec-enabled fixture lives in `tests/agent/test_summaries.py` (`env`); reuse it via import:

```python
async def test_build_session_summarizer_peek_reads_collected_summary(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The helper is the production seam: collect persists, peek reads back."""
    from octave.agent.composition import build_session_summarizer
    from octave.db.models import Participant, SessionParticipant

    async with session_factory() as session:
        session.add(User(id="u_1", username="alice", display_name="Alice"))
        session.add(Participant(id="p_u1", user_id="u_1", label="Alice"))
        session.add(Session(id="s_1", created_by_user_id="u_1", status="active"))
        session.add(SessionParticipant(session_id="s_1", participant_id="p_u1"))
        await EventStore(session).append(
            "s_1", EventKind.USER_MESSAGE, author_participant_id="p_u1",
            payload={"content": "hello"},
        )
        await session.commit()

    scripted = ScriptedAdapter([final_result("Title: Recap\nWe said hello.")])
    async with session_factory() as session:
        summarizer = build_session_summarizer(
            session,
            db_adapter=_vec_adapter(session_factory),
            adapter=scripted,
            adapter_name="fake",
        )
        summary = await summarizer.collect("s_1", force=True)
        await session.commit()
    assert summary is not None and summary.title == "Recap"
    assert len(scripted.requests) == 1
```

**Important — DB fixture:** `VaultStore.upsert` calls `remove_vector`, so the vec0 table must exist. `tests/agent/test_composition.py` currently has no vec tables. Replace its `session_factory` usage for this test with the vec-capable pattern from `tests/agent/test_summaries.py::env` — copy that fixture into the test file as `vec_env` (it yields `(SqliteVecAdapter, async_sessionmaker)`), and use it:

```python
from pathlib import Path
from collections.abc import AsyncIterator

import pytest_asyncio
from octave.db.config import DbConfig
from octave.db.sqlite_adapter import SqliteVecAdapter

@pytest_asyncio.fixture
async def vec_env(
    tmp_path: Path,
) -> AsyncIterator[tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]]:
    config = DbConfig(
        adapter="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'composition.db'}",
        embedding_dim=4,
    )
    adapter = SqliteVecAdapter(config)
    engine = adapter.make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await adapter.ensure_vector_store(conn)
    yield adapter, adapter.make_session_factory(engine)
    await engine.dispose()
```

Then write the test against `vec_env` (unpack `adapter, factory`; seed inside `factory()`; pass `db_adapter=adapter`). Required imports at top of the appended block: `Base`, `EventKind`, `User`, `Session` are already imported in the file; add `EventStore` (already imported), `Participant`/`SessionParticipant` (import inside the test as shown).

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/agent/test_composition.py -k summarizer -v`
Expected: FAIL/ERROR — `ImportError: cannot import name 'build_session_summarizer'`.

- [ ] **Step 3: Implement the helper**

In `backend/src/octave/agent/composition.py`, extend imports and add the function (keep `__all__` sorted):

```python
from octave.agent.errors import ModelBindingError
from octave.agent.summaries import SessionSummarizer
from octave.db.adapter import DbAdapter
from octave.db.types import ExplicitModelBinding
from octave.inference.adapter import InferenceAdapter

__all__ = ["build_message_router", "build_session_summarizer"]
```

```python
def build_session_summarizer(
    session: AsyncSession,
    *,
    db_adapter: DbAdapter,
    adapter: InferenceAdapter | None = None,
    adapter_name: str | None = None,
    tag_lookup: Callable[[str], str | None] | None = None,
) -> SessionSummarizer:
    """Wire the summarizer to the single configured inference adapter.

    ``adapter=None`` keeps the read paths (peek) usable when inference is
    unconfigured; ``adapter_for`` then fails loud on any generation
    attempt, mirroring DefaultTurnRunner's binding guard. The empty model
    string falls through to the adapter's own default at request time.
    """
    name = adapter_name or "openai"
    binding = ExplicitModelBinding(
        kind="explicit",
        adapter=name,
        model=(adapter.config.default_model if adapter else "") or "",
    )

    def adapter_for(requested: str | None) -> InferenceAdapter:
        if adapter is None or requested != name:
            raise ModelBindingError(
                f"summary binding requests adapter {requested!r} but only "
                f"{name!r} is configured"
            )
        return adapter

    return SessionSummarizer(
        session=session,
        db_adapter=db_adapter,
        binding=binding,
        adapter_for=adapter_for,
        tag_lookup=tag_lookup,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/agent/test_composition.py -v && uv run ruff check src tests`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/agent/composition.py backend/tests/agent/test_composition.py
git commit -m "feat(agent): build_session_summarizer composition helper (#112)"
```

---

### Task 2: List endpoint `GET /api/session-summaries`

**Files:**
- Create: `backend/src/octave/routes/summaries.py`
- Modify: `backend/src/octave/app.py`
- Test: `backend/tests/routes/test_summaries.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/routes/test_summaries.py`. The harness mirrors `tests/routes/test_sessions.py::_sessions_app` but must use `SqliteVecAdapter` + `ensure_vector_store` (VaultStore needs the vec0 mirror) and publish `app.state.db_adapter`:

```python
"""Session-summary routes (issue #112).

Harness like tests/routes/test_sessions.py, but vec-enabled: VaultStore
upsert/list touch the vec0 mirror, so app.state.db_adapter is a real
SqliteVecAdapter over the same engine as the session factory.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.auth.config import AuthSettings
from octave.auth.passwords import DummyHasher
from octave.db.config import DbConfig
from octave.db.event_store import EventStore
from octave.db.models import Base, Session, VaultItem
from octave.db.session_store import SessionStore
from octave.db.sqlite_adapter import SqliteVecAdapter
from octave.db.types import EventKind, VaultKind
from octave.middleware import add_error_handlers
from octave.routes.auth import router as auth_router
from octave.routes.summaries import router as summaries_router


def _dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


@pytest_asyncio.fixture
async def app_and_factory(tmp_path: Path):
    config = DbConfig(
        adapter="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'summaries_routes.db'}",
        embedding_dim=4,
    )
    adapter = SqliteVecAdapter(config)
    engine = adapter.make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await adapter.ensure_vector_store(conn)
    factory = adapter.make_session_factory(engine)
    app = FastAPI()
    app.state.db_session_factory = factory
    app.state.db_adapter = adapter
    app.state.auth_hasher = DummyHasher()
    app.state.auth_settings = AuthSettings(cookie_secure=False)
    add_error_handlers(app)
    app.include_router(auth_router, prefix="/api")
    app.include_router(summaries_router, prefix="/api")
    yield app, factory
    await engine.dispose()


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _bootstrap(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/bootstrap",
        json={"username": "alice", "password": "pw", "display_name": "Alice"},
    )
    assert response.status_code == 201


async def _create_session(factory, session_id: str, owner: str = "u_1") -> None:
    async with factory() as session:
        row = Session(id=session_id, created_by_user_id=owner, status="active")
        session.add(row)
        await session.commit()
        row.created_at = _dt(1)


async def _seed_summary(
    factory, db_adapter, session_id: str, *, name: str, content: str
) -> None:
    async with factory() as session:
        from octave.db.vault_store import VaultStore

        await VaultStore(db_adapter, session).upsert(
            item_id=f"session_summary:{session_id}",
            user_id="u_1",
            kind=VaultKind.SESSION_SUMMARY,
            name=name,
            content=content,
            meta={"session_id": session_id, "covered_seq": 2, "model_name": "m-1"},
        )
        await session.commit()


async def _member_participant(factory, user_id: str) -> str:
    from octave.db.models import Participant, SessionParticipant

    async with factory() as session:
        participant = await SessionStore(session).get_participant_for_user(user_id)
        assert participant is not None
        return participant.id


async def _join(factory, session_id: str, participant_id: str) -> None:
    from octave.db.models import SessionParticipant

    async with factory() as session:
        session.add(
            SessionParticipant(session_id=session_id, participant_id=participant_id)
        )
        await session.commit()
```

Then the list tests:

```python
async def test_list_requires_auth(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        assert (await client.get("/api/session-summaries")).status_code == 401


async def test_list_attaches_summary_or_null(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        await _create_session(factory, "s_1")
        await _seed_summary(factory, app.state.db_adapter, "s_1",
                            name="Weather", content="Sunny day, went outside.")
        await _create_session(factory, "s_2")
        response = await client.get("/api/session-summaries")
    assert response.status_code == 200
    page = response.json()
    by_id = {item["id"]: item for item in page["items"]}
    assert by_id["s_1"]["summary"]["title"] == "Weather"
    assert by_id["s_1"]["summary"]["snippet"].startswith("Sunny")
    assert by_id["s_1"]["summary"]["model_name"] == "m-1"
    assert by_id["s_2"]["summary"] is None
    assert page["total"] == 2


async def test_list_never_leaks_other_users_summaries(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        await _create_session(factory, "s_1")
        # A summary owned by a different user, pointing at alice's session:
        # the owner-scoped join must skip it.
        async with factory() as session:
            from octave.db.vault_store import VaultStore

            await VaultStore(app.state.db_adapter, session).upsert(
                item_id="session_summary:s_1", user_id="u_other",
                kind=VaultKind.SESSION_SUMMARY, name="Foreign", content="x",
                meta={"session_id": "s_1", "covered_seq": 1},
            )
            await session.commit()
        page = (await client.get("/api/session-summaries")).json()
    assert page["items"][0]["summary"] is None


async def test_list_skips_malformed_meta_items(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        await _create_session(factory, "s_1")
        async with factory() as session:
            session.add(VaultItem(
                id="session_summary:s_1", user_id="u_1", kind="session_summary",
                name="Broken", content="y", meta={"nope": True},
            ))
            await session.commit()
        page = (await client.get("/api/session-summaries")).json()
    assert page["items"][0]["summary"] is None


async def test_list_pages_summaries_beyond_one_vault_page(app_and_factory) -> None:
    """150 sessions each with a summary: the vault fetch must page past its
    first page (a silent cap would drop summaries from the join)."""
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        async with factory() as session:
            from octave.db.vault_store import VaultStore

            store = VaultStore(app.state.db_adapter, session)
            for i in range(150):
                sid = f"s_{i:03d}"
                session.add(Session(id=sid, created_by_user_id="u_1", status="active"))
                await store.upsert(
                    item_id=f"session_summary:{sid}", user_id="u_1",
                    kind=VaultKind.SESSION_SUMMARY, name=f"T{i}", content=f"c{i}",
                    meta={"session_id": sid, "covered_seq": 1},
                )
            await session.commit()
        page = (await client.get("/api/session-summaries?limit=100")).json()
    assert page["total"] == 150
    assert all(item["summary"] is not None for item in page["items"])


async def test_list_pagination_bounds(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        for i in range(3):
            await _create_session(factory, f"s_{i}")
        page = (await client.get("/api/session-summaries?limit=2&offset=0")).json()
        bad = await client.get("/api/session-summaries?limit=0")
    assert len(page["items"]) == 2 and page["total"] == 3
    assert (page["limit"], page["offset"]) == (2, 0)
    assert bad.status_code == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/routes/test_summaries.py -v`
Expected: collection ERROR — `No module named 'octave.routes.summaries'`.

- [ ] **Step 3: Implement the router (list only for now)**

Create `backend/src/octave/routes/summaries.py`:

```python
"""Session-summary HTTP surface (issue #112).

The result-viewer backend: a list joining the caller's sessions to their
cached summaries, a peek-only detail, and a force-refresh that runs the
LLM. Routes own commits (deps compose ``get_db_session``); the
summarizer never commits. Owner-scoped join (spec Decision 4): a joined
non-owner sees "No summary yet" in the list even when the owner has one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from octave.auth.deps import get_current_user
from octave.db.deps import get_db_session
from octave.db.errors import OwnerParticipantMissingError
from octave.db.models import User, VaultItem
from octave.db.session_store import SessionActivityRow, SessionStore
from octave.db.types import VaultKind
from octave.db.vault_store import VaultStore

__all__ = ["router"]

router = APIRouter(prefix="/session-summaries", tags=["summaries"])

PREVIEW_MAX_CHARS = 140
"""Same route-boundary truncation as GET /api/sessions."""

_VAULT_PAGE = 200
"""Vault fetch page size; the summary join pages to exhaustion (a silent
cap would drop summaries — the ``injection._corpus`` rule)."""


class SummarySnippetResponse(BaseModel):
    title: str
    snippet: str
    model_name: str | None
    generated_at: datetime


class SummaryDetailResponse(BaseModel):
    title: str
    content: str
    model_name: str | None
    generated_at: datetime


class SessionSummaryListItem(BaseModel):
    id: str
    title: str | None
    created_at: datetime
    last_activity: datetime
    last_message: str | None
    summary: SummarySnippetResponse | None


class SessionSummaryListResponse(BaseModel):
    items: list[SessionSummaryListItem]
    total: int
    limit: int
    offset: int


class SessionSummaryDetailResponse(BaseModel):
    session_id: str
    summary: SummaryDetailResponse | None


def _preview(content: str) -> str:
    if len(content) <= PREVIEW_MAX_CHARS:
        return content
    return content[: PREVIEW_MAX_CHARS - 1] + "…"


def _snippet(item: VaultItem) -> SummarySnippetResponse | None:
    """Lenient parse mirroring ``summaries._to_summary``: malformed meta is
    treated as absent; items whose meta.session_id is missing/mismatched
    are skipped."""
    meta = item.meta if isinstance(item.meta, dict) else {}
    session_id = meta.get("session_id")
    expected = item.id.removeprefix("session_summary:")
    if not isinstance(session_id, str) or session_id != expected:
        return None
    model_name = meta.get("model_name")
    return SummarySnippetResponse(
        title=item.name,
        snippet=_preview(item.content),
        model_name=model_name if isinstance(model_name, str) else None,
        generated_at=item.updated_at,
    )


async def _summaries_by_session(
    vault: VaultStore, user_id: str
) -> dict[str, SummarySnippetResponse]:
    """All the owner's summary items, paged to exhaustion, keyed by
    session_id (lenient parse; malformed entries skipped)."""
    out: dict[str, SummarySnippetResponse] = {}
    offset = 0
    while True:
        page = await vault.list_items(
            user_id=user_id, kind=VaultKind.SESSION_SUMMARY,
            limit=_VAULT_PAGE, offset=offset,
        )
        for item in page:
            snippet = _snippet(item)
            if snippet is not None:
                out[item.id.removeprefix("session_summary:")] = snippet
        if len(page) < _VAULT_PAGE:
            return out
        offset += _VAULT_PAGE


async def _require_participant_id(store: SessionStore, user: User) -> str:
    participant = await store.get_participant_for_user(user.id)
    if participant is None:
        raise OwnerParticipantMissingError(user.id)
    return participant.id


@router.get("")
async def list_session_summaries(
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_db_session),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> SessionSummaryListResponse:
    """The caller's sessions (activity-ordered, paginated — same ordering
    as GET /api/sessions) each carrying its cached summary snippet or
    null. Summaries are owner-scoped: joined non-owner sessions show
    null even when the owner has one (spec Decision 4)."""
    store = SessionStore(session)
    participant_id = await _require_participant_id(store, user)
    rows, total = await store.list_sessions(
        participant_id=participant_id, limit=limit, offset=offset
    )
    summaries = await _summaries_by_session(VaultStore(_db_adapter(request), session), user.id)
    return SessionSummaryListResponse(
        items=[
            SessionSummaryListItem(
                id=row.id,
                title=row.title,
                created_at=row.created_at,
                last_activity=row.last_activity,
                last_message=_preview(row.last_message) if row.last_message else None,
                summary=summaries.get(row.id),
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
```

The list route also needs `app.state.db_adapter`. Add a module-level resolver at the top of the router file (Task 3/4 use it too):

```python
from fastapi import Request
from octave.db.adapter import DbAdapter


def _db_adapter(request: Request) -> DbAdapter:
    adapter: DbAdapter | None = getattr(request.app.state, "db_adapter", None)
    if adapter is None:
        raise HTTPException(status_code=503, detail="database adapter not configured")
    return adapter
```

and add `request: Request` as the first parameter of `list_session_summaries` (FastAPI injects it).

- [ ] **Step 4: Mount the router**

In `backend/src/octave/app.py`:

```python
from octave.routes.summaries import router as summaries_router
# ... after the sessions include_router line:
app.include_router(summaries_router, prefix="/api")
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && uv run pytest tests/routes/test_summaries.py -v && uv run ruff check src tests`
Expected: all PASS (5 list tests), ruff clean.

- [ ] **Step 6: Commit**

```bash
git add backend/src/octave/routes/summaries.py backend/src/octave/app.py backend/tests/routes/test_summaries.py
git commit -m "feat(routes): GET /api/session-summaries list with attached summaries (#112)"
```

---

### Task 3: Detail endpoint `GET /api/session-summaries/{session_id}`

**Files:**
- Modify: `backend/src/octave/routes/summaries.py`
- Test: `backend/tests/routes/test_summaries.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_summaries.py`:

```python
async def test_detail_requires_auth(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        assert (await client.get("/api/session-summaries/s_1")).status_code == 401


async def test_detail_404_unknown_403_non_member(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        assert (await client.get("/api/session-summaries/nope")).status_code == 404
        await _create_session(factory, "s_1")
        # bob exists but never joined s_1.
        created = await client.post(
            "/api/auth/users",
            json={"username": "bob", "password": "pw2", "display_name": "Bob",
                  "role": "member"},
        )
        assert created.status_code == 201
        bob = await _client(app)
        login = await bob.post(
            "/api/auth/login", json={"username": "bob", "password": "pw2"}
        )
        assert login.status_code == 200
        assert (await bob.get("/api/session-summaries/s_1")).status_code == 403
        await bob.aclose()


async def test_detail_returns_null_without_cache(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        await _create_session(factory, "s_1")
        response = await client.get("/api/session-summaries/s_1")
    assert response.status_code == 200
    assert response.json() == {"session_id": "s_1", "summary": None}


async def test_detail_returns_full_content_from_cache(app_and_factory) -> None:
    app, factory = app_and_factory
    long_body = "x" * 300
    async with await _client(app) as client:
        await _bootstrap(client)
        await _create_session(factory, "s_1")
        await _seed_summary(factory, app.state.db_adapter, "s_1",
                            name="Weather", content=long_body)
        response = await client.get("/api/session-summaries/s_1")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["title"] == "Weather"
    assert body["summary"]["content"] == long_body  # full, not truncated
    assert body["summary"]["model_name"] == "m-1"


async def test_detail_never_calls_inference(app_and_factory) -> None:
    """No app.state.inference_adapter at all: peek still answers 200."""
    app, factory = app_and_factory
    assert not hasattr(app.state, "inference_adapter")
    async with await _client(app) as client:
        await _bootstrap(client)
        await _create_session(factory, "s_1")
        await _seed_summary(factory, app.state.db_adapter, "s_1",
                            name="Weather", content="Sunny.")
        response = await client.get("/api/session-summaries/s_1")
    assert response.status_code == 200
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/routes/test_summaries.py -k detail -v`
Expected: FAIL — 404 route-not-found (`{"error": "Not Found"}`) on the detail URL for authed requests.

- [ ] **Step 3: Implement**

In `routes/summaries.py`, add imports:

```python
from octave.agent.composition import build_session_summarizer
from octave.agent.summaries import SessionSummary
```

and the endpoint + a shared detail serializer:

```python
def _detail(summary: SessionSummary) -> SummaryDetailResponse:
    return SummaryDetailResponse(
        title=summary.title,
        content=summary.content,
        model_name=summary.model_name,
        generated_at=summary.generated_at,
    )


async def _access_gate(session: AsyncSession, session_id: str, user: User) -> None:
    """Owner or live participant may view/refresh a summary — 404 unknown,
    403 forbidden (mirrors the transcript route and the WS join guard)."""
    verdict = await SessionStore(session).resolve_access(session_id, user.id)
    if verdict == "missing":
        raise HTTPException(status_code=404, detail="Session not found")
    if verdict == "denied":
        raise HTTPException(status_code=403, detail="Not a member of this session")


@router.get("/{session_id}")
async def get_session_summary(
    request: Request,
    session_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_db_session),
) -> SessionSummaryDetailResponse:
    """Cached summary only via ``peek`` — never calls the adapter, so the
    route answers even with inference unconfigured."""
    await _access_gate(session, session_id, user)
    summarizer = build_session_summarizer(
        session, db_adapter=_db_adapter(request)
    )
    summary = await summarizer.peek(session_id)
    return SessionSummaryDetailResponse(
        session_id=session_id,
        summary=_detail(summary) if summary is not None else None,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/routes/test_summaries.py -v && uv run ruff check src tests`
Expected: all PASS (list + detail), ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/routes/summaries.py backend/tests/routes/test_summaries.py
git commit -m "feat(routes): session-summary detail via peek (#112)"
```

---

### Task 4: Refresh endpoint `POST /api/session-summaries/{session_id}/refresh`

**Files:**
- Modify: `backend/src/octave/routes/summaries.py`
- Test: `backend/tests/routes/test_summaries.py`

- [ ] **Step 1: Write the failing tests**

Append to `test_summaries.py` (imports needed at top of file):

```python
from octave.inference.types import CompletionResult
from tests.agent.fakes import ScriptedAdapter
```

```python
def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="scripted-1", finish_reason="stop")


async def test_refresh_requires_auth(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        response = await client.post("/api/session-summaries/s_1/refresh")
        assert response.status_code == 401


async def test_refresh_happy_path_persists_and_returns_detail(
    app_and_factory,
) -> None:
    app, factory = app_and_factory
    app.state.inference_adapter = ScriptedAdapter(
        [_completion("Title: Greeting\nAlice greeted; Echo replied.")]
    )
    app.state.inference_adapter_name = "fake"
    async with await _client(app) as client:
        await _bootstrap(client)
        await _create_session(factory, "s_1")
        async with factory() as session:
            await EventStore(session).append(
                "s_1", EventKind.USER_MESSAGE, payload={"content": "hello"}
            )
            await session.commit()
        response = await client.post("/api/session-summaries/s_1/refresh")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["title"] == "Greeting"
    assert body["summary"]["content"] == "Alice greeted; Echo replied."
    assert body["summary"]["model_name"] == "scripted-1"
    # Committed: a fresh session sees the vault item.
    async with factory() as session:
        from octave.db.vault_store import VaultStore

        item = await VaultStore(app.state.db_adapter, session).get(
            "session_summary:s_1"
        )
    assert item is not None and item.name == "Greeting"


async def test_refresh_force_regenerates_over_fresh_cache(app_and_factory) -> None:
    app, factory = app_and_factory
    scripted = ScriptedAdapter(
        [_completion("Title: One\nfirst."), _completion("Title: Two\nsecond.")]
    )
    app.state.inference_adapter = scripted
    app.state.inference_adapter_name = "fake"
    async with await _client(app) as client:
        await _bootstrap(client)
        await _create_session(factory, "s_1")
        async with factory() as session:
            await EventStore(session).append(
                "s_1", EventKind.USER_MESSAGE, payload={"content": "hello"}
            )
            await session.commit()
        first = await client.post("/api/session-summaries/s_1/refresh")
        second = await client.post("/api/session-summaries/s_1/refresh")
    assert first.json()["summary"]["title"] == "One"
    assert second.json()["summary"]["title"] == "Two"  # force=True, cache bypassed


async def test_refresh_empty_transcript_returns_null(app_and_factory) -> None:
    app, factory = app_and_factory
    scripted = ScriptedAdapter([])  # queue empty: any call would AssertionError
    app.state.inference_adapter = scripted
    app.state.inference_adapter_name = "fake"
    async with await _client(app) as client:
        await _bootstrap(client)
        await _create_session(factory, "s_1")
        response = await client.post("/api/session-summaries/s_1/refresh")
    assert response.status_code == 200
    assert response.json() == {"session_id": "s_1", "summary": None}


async def test_refresh_summary_error_maps_to_502(app_and_factory) -> None:
    app, factory = app_and_factory
    app.state.inference_adapter = ScriptedAdapter([_completion("")])  # empty → SummaryError
    app.state.inference_adapter_name = "fake"
    async with await _client(app) as client:
        await _bootstrap(client)
        await _create_session(factory, "s_1")
        async with factory() as session:
            await EventStore(session).append(
                "s_1", EventKind.USER_MESSAGE, payload={"content": "hello"}
            )
            await session.commit()
        response = await client.post("/api/session-summaries/s_1/refresh")
    assert response.status_code == 502


async def test_refresh_without_inference_adapter_is_503(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        await _create_session(factory, "s_1")
        response = await client.post("/api/session-summaries/s_1/refresh")
    assert response.status_code == 503


async def test_refresh_gate_precedes_adapter_check(app_and_factory) -> None:
    """Unknown session with no inference configured: 404 (gate first),
    never 503."""
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        response = await client.post("/api/session-summaries/nope/refresh")
    assert response.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/routes/test_summaries.py -k refresh -v`
Expected: FAIL — 404/405 on the refresh URLs.

- [ ] **Step 3: Implement**

In `routes/summaries.py`, add imports:

```python
from octave.agent.errors import SessionNotFoundError, SummaryError
```

and the endpoint (place after the detail route; the static path `/{session_id}/refresh` does not collide with `/{session_id}` because FastAPI matches methods and full paths):

```python
@router.post("/{session_id}/refresh")
async def refresh_session_summary(
    request: Request,
    session_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_db_session),
) -> SessionSummaryDetailResponse:
    """Force-regenerate the summary via the LLM (``collect(force=True)``)
    and commit. Empty transcript → null (nothing to summarize, not an
    error). Upstream generation failure → 502; inference unconfigured →
    503 (Octave boots fine without it — list/detail still answer)."""
    await _access_gate(session, session_id, user)
    adapter = getattr(request.app.state, "inference_adapter", None)
    if adapter is None:
        raise HTTPException(
            status_code=503, detail="Inference adapter not configured"
        )
    summarizer = build_session_summarizer(
        session,
        db_adapter=_db_adapter(request),
        adapter=adapter,
        adapter_name=getattr(request.app.state, "inference_adapter_name", None),
    )
    try:
        summary = await summarizer.collect(session_id, force=True)
    except SessionNotFoundError as exc:  # defensive; the gate covers it
        raise HTTPException(status_code=404, detail="Session not found") from exc
    except SummaryError as exc:
        raise HTTPException(
            status_code=502, detail=f"Summary generation failed: {exc}"
        ) from exc
    await session.commit()
    return SessionSummaryDetailResponse(
        session_id=session_id,
        summary=_detail(summary) if summary is not None else None,
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && uv run pytest tests/routes/test_summaries.py tests/agent/test_composition.py -v && uv run ruff check src tests`
Expected: all PASS, ruff clean.

- [ ] **Step 5: Full backend suite + commit**

Run: `cd backend && uv run pytest tests/ -v`
Expected: all green (no regressions).

```bash
git add backend/src/octave/routes/summaries.py backend/tests/routes/test_summaries.py
git commit -m "feat(routes): session-summary refresh via collect(force=True) (#112)"
```

---

### Task 5: Frontend API module `lib/api/summaries.ts`

**Files:**
- Create: `frontend/src/lib/api/summaries.ts`
- Test: `frontend/src/lib/api/summaries.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/api/summaries.test.ts` (fetch-stub pattern mirrors `sessions.test.ts`):

```ts
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  fetchSessionSummaries,
  fetchSessionSummaryDetail,
  refreshSessionSummary,
} from './summaries';

const fetchMock = vi.fn();

function jsonResponse(payload: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(payload),
  } as Response;
}

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe('summaries api', () => {
  it('fetches the summary list page', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ items: [], total: 0, limit: 20, offset: 0 })
    );
    const page = await fetchSessionSummaries(20, 0);
    expect(page.total).toBe(0);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain('/api/session-summaries?limit=20&offset=0');
    expect(init.method).toBe('GET');
    expect(init.credentials).toBe('include');
  });

  it('fetches one session summary detail', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ session_id: 's/1', summary: null }));
    const res = await fetchSessionSummaryDetail('s/1');
    expect(res.session_id).toBe('s/1');
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toContain('/api/session-summaries/s%2F1');
  });

  it('refreshes a session summary via POST', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ session_id: 's_1', summary: { title: 'T', content: 'C', model_name: null, generated_at: '2026-11-26T00:00:00Z' } })
    );
    const res = await refreshSessionSummary('s_1');
    expect(res.summary?.title).toBe('T');
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain('/api/session-summaries/s_1/refresh');
    expect(init.method).toBe('POST');
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/lib/api/summaries.test.ts`
Expected: FAIL — cannot resolve `./summaries`.

- [ ] **Step 3: Implement**

Create `frontend/src/lib/api/summaries.ts`:

```ts
// Session-summary API surface (issue #112). Mirrors the backend contract in
// backend/src/octave/routes/summaries.py: list = caller's sessions with the
// cached summary snippet attached (or null); detail = peek (never triggers
// inference); refresh = collect(force=True) via the LLM.
import { get, post } from './client';

export interface SummarySnippet {
  title: string;
  /** Route-truncated body (<= 140 chars). */
  snippet: string;
  model_name: string | null;
  /** ISO-8601 datetime. */
  generated_at: string;
}

export interface SummaryDetail {
  title: string;
  content: string;
  model_name: string | null;
  generated_at: string;
}

export interface SessionSummaryListItem {
  id: string;
  title: string | null;
  created_at: string;
  last_activity: string;
  last_message: string | null;
  summary: SummarySnippet | null;
}

export interface SessionSummaryListPage {
  items: SessionSummaryListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface SessionSummaryDetailResponse {
  session_id: string;
  summary: SummaryDetail | null;
}

export async function fetchSessionSummaries(
  limit = 20,
  offset = 0
): Promise<SessionSummaryListPage> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return get<SessionSummaryListPage>(`/api/session-summaries?${params}`);
}

export async function fetchSessionSummaryDetail(
  sessionId: string
): Promise<SessionSummaryDetailResponse> {
  return get<SessionSummaryDetailResponse>(
    `/api/session-summaries/${encodeURIComponent(sessionId)}`
  );
}

export async function refreshSessionSummary(
  sessionId: string
): Promise<SessionSummaryDetailResponse> {
  return post<SessionSummaryDetailResponse>(
    `/api/session-summaries/${encodeURIComponent(sessionId)}/refresh`
  );
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/lib/api/summaries.test.ts && npx tsc -b`
Expected: PASS, typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api/summaries.ts frontend/src/lib/api/summaries.test.ts
git commit -m "feat(frontend): session-summaries api module (#112)"
```

---

### Task 6: `useSessionSummaries` hook

**Files:**
- Create: `frontend/src/lib/api/useSessionSummaries.ts`
- Test: `frontend/src/lib/api/useSessionSummaries.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/api/useSessionSummaries.test.ts`:

```ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { ApiError } from './client';
import { useSessionSummaries } from './useSessionSummaries';
import * as summariesApi from './summaries';

vi.mock('./summaries');

const row = (id: string): summariesApi.SessionSummaryListItem => ({
  id,
  title: `Chat ${id}`,
  created_at: '2026-11-01T00:00:00Z',
  last_activity: '2026-11-24T12:00:00Z',
  last_message: 'preview',
  summary: null,
});

const page = (ids: string[], total: number): summariesApi.SessionSummaryListPage => ({
  items: ids.map(row),
  total,
  limit: 20,
  offset: 0,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useSessionSummaries', () => {
  it('loads the first page on mount', async () => {
    vi.mocked(summariesApi.fetchSessionSummaries).mockResolvedValue(page(['a', 'b'], 2));
    const { result } = renderHook(() => useSessionSummaries());
    expect(result.current.state).toBe('loading');
    await waitFor(() => expect(result.current.state).toBe('ready'));
    expect(result.current.items.map((i) => i.id)).toEqual(['a', 'b']);
    expect(result.current.hasMore).toBe(false);
  });

  it('surfaces fetch errors with a working retry', async () => {
    vi.mocked(summariesApi.fetchSessionSummaries)
      .mockRejectedValueOnce(new ApiError(500, 'boom'))
      .mockResolvedValueOnce(page(['a'], 1));
    const { result } = renderHook(() => useSessionSummaries());
    await waitFor(() => expect(result.current.state).toBe('error'));
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.state).toBe('ready'));
  });

  it('regenerate patches the row summary in place', async () => {
    vi.mocked(summariesApi.fetchSessionSummaries).mockResolvedValue(page(['a'], 1));
    vi.mocked(summariesApi.refreshSessionSummary).mockResolvedValue({
      session_id: 'a',
      summary: { title: 'T', content: 'C', model_name: 'm', generated_at: '2026-11-26T00:00:00Z' },
    });
    const { result } = renderHook(() => useSessionSummaries());
    await waitFor(() => expect(result.current.state).toBe('ready'));
    await act(async () => {
      await result.current.regenerate('a');
    });
    expect(result.current.items[0].summary?.title).toBe('T');
  });

  it('regenerate throws and sets the error on failure', async () => {
    vi.mocked(summariesApi.fetchSessionSummaries).mockResolvedValue(page(['a'], 1));
    vi.mocked(summariesApi.refreshSessionSummary).mockRejectedValue(new ApiError(503, 'off'));
    const { result } = renderHook(() => useSessionSummaries());
    await waitFor(() => expect(result.current.state).toBe('ready'));
    await expect(
      act(async () => {
        await result.current.regenerate('a');
      })
    ).rejects.toThrow();
    expect(result.current.error).toContain("Couldn't generate summary");
  });

  it('loadOlder appends the next page', async () => {
    vi.mocked(summariesApi.fetchSessionSummaries)
      .mockResolvedValueOnce(page(['a'], 2))
      .mockResolvedValueOnce({ ...page(['b'], 2), offset: 1, items: [row('b')] });
    const { result } = renderHook(() => useSessionSummaries(1));
    await waitFor(() => expect(result.current.state).toBe('ready'));
    expect(result.current.hasMore).toBe(true);
    act(() => result.current.loadOlder());
    await waitFor(() => expect(result.current.items.map((i) => i.id)).toEqual(['a', 'b']));
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/lib/api/useSessionSummaries.test.ts`
Expected: FAIL — cannot resolve `./useSessionSummaries`.

- [ ] **Step 3: Implement**

Create `frontend/src/lib/api/useSessionSummaries.ts` (cloned discipline from `useConversations.ts`: stale-response guard, ref mirror, `describeListError`-style copy):

```ts
// Session-summary list hook (issue #112). Same discipline as
// useConversations: stale-response guard via requestRef, a ref mirror for
// synchronous reads inside callbacks, and errors that never silently pass.
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './client';
import {
  fetchSessionSummaries,
  refreshSessionSummary,
  type SessionSummaryListItem,
  type SummaryDetail,
} from './summaries';

export type SummariesState = 'loading' | 'ready' | 'error';

export interface UseSessionSummaries {
  items: SessionSummaryListItem[];
  state: SummariesState;
  error: string | null;
  hasMore: boolean;
  loadingOlder: boolean;
  refresh: () => void;
  loadOlder: () => void;
  /** POST refresh + patch the row's summary in place. Throws on failure;
   * resolves to the fresh detail (null for an empty transcript). */
  regenerate: (sessionId: string) => Promise<SummaryDetail | null>;
}

function describeError(error: unknown, action: string): string {
  if (error instanceof ApiError) {
    return `Couldn't ${action} (${error.status}).`;
  }
  return `Couldn't ${action} — server unreachable.`;
}

export function useSessionSummaries(pageSize = 20): UseSessionSummaries {
  const [items, setItems] = useState<SessionSummaryListItem[]>([]);
  const [state, setState] = useState<SummariesState>('loading');
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const requestRef = useRef(0);
  const itemsRef = useRef<SessionSummaryListItem[]>([]);

  const refresh = useCallback(() => {
    const requestId = ++requestRef.current;
    setState('loading');
    setError(null);
    fetchSessionSummaries(pageSize, 0)
      .then((page) => {
        if (requestRef.current !== requestId) return;
        itemsRef.current = page.items;
        setItems(page.items);
        setTotal(page.total);
        setState('ready');
      })
      .catch((err: unknown) => {
        if (requestRef.current !== requestId) return;
        setError(describeError(err, 'load summaries'));
        setState('error');
      });
  }, [pageSize]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const loadOlder = useCallback(() => {
    const nextOffset = itemsRef.current.length;
    if (loadingOlder || nextOffset >= total) return;
    const requestId = requestRef.current;
    setLoadingOlder(true);
    fetchSessionSummaries(pageSize, nextOffset)
      .then((page) => {
        if (requestRef.current !== requestId) return;
        itemsRef.current = [...itemsRef.current, ...page.items];
        setItems(itemsRef.current);
        setTotal(page.total);
      })
      .catch((err: unknown) => {
        if (requestRef.current !== requestId) return;
        setError(describeError(err, 'load older summaries'));
      })
      .finally(() => setLoadingOlder(false));
  }, [loadingOlder, total, pageSize]);

  const regenerate = useCallback(async (sessionId: string): Promise<SummaryDetail | null> => {
    let response;
    try {
      response = await refreshSessionSummary(sessionId);
    } catch (err) {
      setError(describeError(err, 'generate summary'));
      throw err;
    }
    const summary = response.summary;
    itemsRef.current = itemsRef.current.map((item) =>
      item.id === sessionId
        ? {
            ...item,
            summary: summary
              ? {
                  title: summary.title,
                  snippet:
                    summary.content.length <= 140
                      ? summary.content
                      : summary.content.slice(0, 139) + '…',
                  model_name: summary.model_name,
                  generated_at: summary.generated_at,
                }
              : null
          }
        : item
    );
    setItems(itemsRef.current);
    return summary;
  }, []);

  return {
    items,
    state,
    error,
    hasMore: items.length < total,
    loadingOlder,
    refresh,
    loadOlder,
    regenerate,
  };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/lib/api/useSessionSummaries.test.ts && npx tsc -b`
Expected: PASS, typecheck clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api/useSessionSummaries.ts frontend/src/lib/api/useSessionSummaries.test.ts
git commit -m "feat(frontend): useSessionSummaries list hook (#112)"
```

---

### Task 7: `AgentsView` master–detail + route swap

**Files:**
- Create: `frontend/src/views/AgentsView.tsx`
- Create: `frontend/src/views/AgentsView.module.css`
- Modify: `frontend/src/router.tsx` (`agentsRoute` component)
- Test: `frontend/tests/views/AgentsView.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/views/AgentsView.test.tsx`:

```tsx
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AgentsView from '../../src/views/AgentsView';
import * as summariesApi from '../../src/lib/api/summaries';
import { ApiError } from '../../src/lib/api/client';

vi.mock('../../src/lib/api/summaries');
const navigate = vi.fn();
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}));

const row = (overrides: Partial<summariesApi.SessionSummaryListItem> = {}) => ({
  id: 's_1',
  title: 'Weather check',
  created_at: '2026-11-01T00:00:00Z',
  last_activity: '2026-11-24T12:00:00Z',
  last_message: 'hello',
  summary: {
    title: 'Weather',
    snippet: 'Sunny day.',
    model_name: 'llama',
    generated_at: '2026-11-24T13:00:00Z',
  },
  ...overrides,
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(summariesApi.fetchSessionSummaries).mockResolvedValue({
    items: [row(), row({ id: 's_2', title: null, summary: null })],
    total: 2,
    limit: 20,
    offset: 0,
  });
  vi.mocked(summariesApi.fetchSessionSummaryDetail).mockResolvedValue({
    session_id: 's_1',
    summary: {
      title: 'Weather',
      content: 'Checked the weather: sunny.',
      model_name: 'llama',
      generated_at: '2026-11-24T13:00:00Z',
    },
  });
});

describe('AgentsView', () => {
  it('renders the list with snippet and no-summary states', async () => {
    render(<AgentsView />);
    expect(await screen.findByRole('button', { name: /Weather check/ })).toBeDefined();
    expect(screen.getByText('Sunny day.')).toBeDefined();
    expect(screen.getByText('Untitled')).toBeDefined();
    expect(screen.getAllByText('No summary yet').length).toBeGreaterThan(0);
  });

  it('selecting a row fetches and shows the detail', async () => {
    render(<AgentsView />);
    await userEvent.click(await screen.findByRole('button', { name: /Weather check/ }));
    expect(await screen.findByText('Checked the weather: sunny.')).toBeDefined();
    expect(summariesApi.fetchSessionSummaryDetail).toHaveBeenCalledWith('s_1');
  });

  it('refresh regenerates and patches the detail', async () => {
    vi.mocked(summariesApi.refreshSessionSummary).mockResolvedValue({
      session_id: 's_1',
      summary: {
        title: 'Weather v2',
        content: 'Regenerated recap.',
        model_name: 'llama',
        generated_at: '2026-11-26T00:00:00Z',
      },
    });
    render(<AgentsView />);
    await userEvent.click(await screen.findByRole('button', { name: /Weather check/ }));
    await screen.findByText('Checked the weather: sunny.');
    await userEvent.click(screen.getByRole('button', { name: /refresh/i }));
    expect(await screen.findByText('Regenerated recap.')).toBeDefined();
    expect(summariesApi.refreshSessionSummary).toHaveBeenCalledWith('s_1');
  });

  it('refresh failure surfaces inline', async () => {
    vi.mocked(summariesApi.refreshSessionSummary).mockRejectedValue(new ApiError(503, 'off'));
    render(<AgentsView />);
    await userEvent.click(await screen.findByRole('button', { name: /Weather check/ }));
    await screen.findByText('Checked the weather: sunny.');
    await userEvent.click(screen.getByRole('button', { name: /refresh/i }));
    expect(await screen.findByRole('alert')).textContent?.toContain('503');
  });

  it('open chat navigates to the session transcript', async () => {
    render(<AgentsView />);
    await userEvent.click(await screen.findByRole('button', { name: /Weather check/ }));
    await screen.findByText('Checked the weather: sunny.');
    await userEvent.click(screen.getByRole('link', { name: /open chat/i }));
    expect(navigate).toHaveBeenCalledWith({
      to: '/sessions/$sessionId',
      params: { sessionId: 's_1' },
    });
  });

  it('empty list shows the empty state', async () => {
    vi.mocked(summariesApi.fetchSessionSummaries).mockResolvedValue({
      items: [], total: 0, limit: 20, offset: 0,
    });
    render(<AgentsView />);
    expect(await screen.findByText('No conversations yet.')).toBeDefined();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run tests/views/AgentsView.test.tsx`
Expected: FAIL — cannot resolve `../../src/views/AgentsView`.

- [ ] **Step 3: Implement the view**

Create `frontend/src/views/AgentsView.module.css` (tokens mirror `ConversationList.module.css`):

```css
.page {
  display: flex;
  gap: 24px;
  align-items: flex-start;
}

.listPane {
  width: 320px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detailPane {
  flex: 1;
  min-width: 0;
}

.row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  text-align: left;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.row:hover {
  background: var(--color-accent-soft, rgba(139, 124, 246, 0.14));
}

.rowSelected {
  border-color: var(--color-accent, #8b7cf6);
  background: var(--color-accent-soft, rgba(139, 124, 246, 0.14));
}

.rowTitle {
  font-weight: 600;
}

.rowSnippet {
  font-size: 0.85rem;
  opacity: 0.75;
}

.status {
  opacity: 0.7;
  font-size: 0.9rem;
}

.detailHeader {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.detailMeta {
  font-size: 0.8rem;
  opacity: 0.65;
  margin-bottom: 16px;
}

.detailBody {
  white-space: pre-wrap;
  line-height: 1.55;
}
```

Create `frontend/src/views/AgentsView.tsx`:

```tsx
// Agent Manager result viewer (issue #112): master–detail over the caller's
// sessions and their cached session summaries. Selecting a row peeks the
// cached summary (never triggers inference); Refresh runs collect(force=True).
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { Button } from '../components/ui';
import { ApiError } from '../lib/api/client';
import { useSessionSummaries } from '../lib/api/useSessionSummaries';
import {
  fetchSessionSummaryDetail,
  type SummaryDetail,
} from '../lib/api/summaries';
import styles from './AgentsView.module.css';

function describeError(error: unknown, action: string): string {
  if (error instanceof ApiError) {
    return `Couldn't ${action} (${error.status}).`;
  }
  return `Couldn't ${action} — server unreachable.`;
}

export default function AgentsView() {
  const navigate = useNavigate();
  const { items, state, error, hasMore, loadingOlder, refresh, loadOlder, regenerate } =
    useSessionSummaries();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SummaryDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const detailRequestRef = useRef(0);

  const loadDetail = useCallback((sessionId: string) => {
    const requestId = ++detailRequestRef.current;
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    fetchSessionSummaryDetail(sessionId)
      .then((res) => {
        if (detailRequestRef.current !== requestId) return;
        setDetail(res.summary);
      })
      .catch((err: unknown) => {
        if (detailRequestRef.current !== requestId) return;
        setDetailError(describeError(err, 'load summary'));
      })
      .finally(() => {
        if (detailRequestRef.current === requestId) setDetailLoading(false);
      });
  }, []);

  const onSelect = useCallback(
    (sessionId: string) => {
      setSelectedId(sessionId);
      loadDetail(sessionId);
    },
    [loadDetail]
  );

  const onRefresh = useCallback(() => {
    if (selectedId === null) return;
    setRefreshing(true);
    setDetailError(null);
    regenerate(selectedId)
      .then((summary) => {
        // Keep the open detail in sync with the freshly generated summary.
        if (summary !== null) setDetail(summary);
      })
      .catch((err: unknown) => setDetailError(describeError(err, 'generate summary')))
      .finally(() => setRefreshing(false));
  }, [regenerate, selectedId]);

  useEffect(() => {
    // Re-fetch the open detail after a full list refresh so stale content
    // never lingers.
    if (selectedId !== null && state === 'ready') loadDetail(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  return (
    <section>
      <h2>Agents</h2>
      <div className={styles.page}>
        <div className={styles.listPane}>
          {state === 'loading' && (
            <p className={styles.status} role="status">
              Loading results…
            </p>
          )}
          {state === 'error' && (
            <p role="alert">
              {error} <Button onClick={refresh}>Retry</Button>
            </p>
          )}
          {state === 'ready' && items.length === 0 && (
            <p className={styles.status}>No conversations yet.</p>
          )}
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`${styles.row} ${selectedId === item.id ? styles.rowSelected : ''}`}
              onClick={() => onSelect(item.id)}
            >
              <span className={styles.rowTitle}>{item.title ?? 'Untitled'}</span>
              <span className={styles.rowSnippet}>
                {item.summary ? item.summary.snippet : 'No summary yet'}
              </span>
            </button>
          ))}
          {hasMore && (
            <Button variant="secondary" size="sm" onClick={loadOlder} disabled={loadingOlder}>
              {loadingOlder ? 'Loading…' : 'Load older'}
            </Button>
          )}
        </div>

        <div className={styles.detailPane}>
          {selectedId === null && (
            <p className={styles.status}>Select a session to see its summary.</p>
          )}
          {selectedId !== null && detailLoading && (
            <p className={styles.status} role="status">
              Loading summary…
            </p>
          )}
          {selectedId !== null && detailError !== null && <p role="alert">{detailError}</p>}
          {selectedId !== null && !detailLoading && detailError === null && detail === null && (
            <>
              <p className={styles.status}>No summary yet.</p>
              <Button onClick={onRefresh} disabled={refreshing}>
                {refreshing ? 'Generating…' : 'Generate summary'}
              </Button>
            </>
          )}
          {selectedId !== null && !detailLoading && detailError === null && detail !== null && (
            <>
              <div className={styles.detailHeader}>
                <h3>{detail.title}</h3>
                <Button onClick={onRefresh} disabled={refreshing} variant="secondary" size="sm">
                  {refreshing ? 'Regenerating…' : 'Refresh'}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    navigate({ to: '/sessions/$sessionId', params: { sessionId: selectedId } })
                  }
                >
                  Open chat
                </Button>
              </div>
              <p className={styles.detailMeta}>
                {detail.model_name ?? 'unknown model'} ·{' '}
                {new Date(detail.generated_at).toLocaleString()}
              </p>
              <div className={styles.detailBody}>{detail.content}</div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Swap the route**

In `frontend/src/router.tsx`: add `import AgentsView from './views/AgentsView';` and replace the `agentsRoute` component:

```tsx
const agentsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/agents',
  staticData: { title: 'Agents' },
  component: AgentsView,
});
```

- [ ] **Step 5: Run view tests + update the App route test**

Run: `cd frontend && npx vitest run tests/views/AgentsView.test.tsx`
Expected: PASS.

Update `frontend/tests/App.test.tsx`: add a summaries api mock beside the sessions mock (the view fetches on mount), and re-point the `/agents` test:

```ts
const summariesMocks = vi.hoisted(() => ({
  fetchSessionSummaries: vi.fn(),
  fetchSessionSummaryDetail: vi.fn(),
  refreshSessionSummary: vi.fn(),
}));
vi.mock('../src/lib/api/summaries', () => summariesMocks);
vi.mocked(summariesMocks.fetchSessionSummaries).mockResolvedValue({
  items: [], total: 0, limit: 20, offset: 0,
});
```

```tsx
  it('renders the Agents result viewer at /agents', async () => {
    renderAt('/agents');
    expect(await screen.findByRole('heading', { level: 2, name: 'Agents' })).toBeDefined();
    expect(
      screen.queryByText('Agent dashboard and result viewer land in the Agent Manager UI issues.')
    ).toBeNull();
  });
```

Run: `cd frontend && npx vitest run tests/App.test.tsx tests/views/AgentsView.test.tsx && npx tsc -b && npm run lint`
Expected: PASS, typecheck + lint clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/views/AgentsView.tsx frontend/src/views/AgentsView.module.css frontend/src/router.tsx frontend/tests/views/AgentsView.test.tsx frontend/tests/App.test.tsx
git commit -m "feat(frontend): Agents result viewer master-detail at /agents (#112)"
```

---

### Task 8: Full verification

- [ ] **Step 1: Backend suite**

Run: `cd backend && uv run pytest tests/ && uv run ruff check src tests`
Expected: all green, ruff clean.

- [ ] **Step 2: Frontend suite + build + lint**

Run: `cd frontend && npm test && npx tsc -b && npm run lint`
Expected: all green.

- [ ] **Step 3: Confirm the PR branch is pushed**

```bash
git push
```

(Then update the PR #165 body with a summary — see the `pr` skill.)

---

## Self-Review (completed by plan author)

1. **Spec coverage:** list (Task 2, incl. owner-scoping, malformed meta, paging-to-exhaustion, bounds), detail/peek (Task 3, incl. never-calls-inference), refresh (Task 4: happy/force/empty→null/502/503/gate-precedence), composition helper (Task 1), frontend api/hook/view/router (Tasks 5–7). Spec error table fully mapped. ✅
2. **Placeholder scan:** no TBD/TODO/"similar to"; every code step is complete. ✅
3. **Type consistency:** `build_session_summarizer(session, *, db_adapter, adapter, adapter_name, tag_lookup)` identical in Tasks 1/3/4; `SummaryDetail`/`SessionSummaryListItem`/`SessionSummaryDetailResponse` identical in Tasks 5/6/7; backend response field names (`snippet`, `model_name`, `generated_at`) match the TS interfaces. ✅
