# Agent Result Collection (Session Summaries) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `SessionSummarizer` — an LLM-written, cached, refreshable summary per session (`VaultKind.SESSION_SUMMARY` vault item), plus the `RUN_SUMMARY → SESSION_SUMMARY` / `RUN_RECORD → TRANSCRIPT_CHUNK` enum rename.

**Architecture:** Library-level only (no routes/UI), per spec `.agents/specs/2026-09-29-agent-result-collection-design.md`. The transcript (`events`, shipped) is the record; the summarizer reads it out-of-band, renders model-facing material through a swappable `TranscriptDigest` (default `HeadTailDigest`), calls one completion through the shipped `ModelBinding`/`resolve_model` machinery, and upserts a deterministic vault item `session_summary:<session_id>` via `VaultStore` (never commits).

**Tech Stack:** Python 3.12, SQLAlchemy 2 async, Pydantic, pytest + pytest-asyncio (auto mode), SQLite+vec0, uv. Run everything from `backend/`.

**Deviations from spec (recorded):**
1. `SessionSummarizer.__init__` takes an extra `db_adapter: DbAdapter` parameter — `VaultStore` requires one ([`vault_store.py:52`](backend/src/octave/db/vault_store.py)). The spec's constructor listing omitted it.
2. The spec's git-commit of the design doc rides Task 1's commit (architect mode has no shell).

**Conventions for every task:**
- Test command: `cd backend && uv run pytest <file> -v`
- Lint/type gate before commit: `cd backend && uv run ruff check src tests && uv run mypy src`
- Commit format: `type(scope): description` (see [`.agents/rules/coding.md`](.agents/rules/coding.md))

---

### Task 1: Rename `VaultKind` run kinds

Zero rows exist for either kind (no writer shipped before this PR); `kind` is app-validated TEXT — enum + tests + docstring only, no migration.

**Files:**
- Modify: `backend/src/octave/db/types.py:56-57`
- Modify: `backend/src/octave/db/models/vault.py:29-30`
- Modify: `backend/tests/db/test_types.py:58-59,69,77-78`
- Modify: `backend/tests/db/test_vault_store.py:233,238,245`

- [ ] **Step 1: Update the failing tests first**

In `backend/tests/db/test_types.py`, change `test_vault_kind_wire_values` (lines 54-59) to:

```python
def test_vault_kind_wire_values() -> None:
    assert VaultKind.SKILL == "skill"
    assert VaultKind.PROMPT == "prompt"
    assert VaultKind.PREFERENCE == "preference"
    assert VaultKind.SESSION_SUMMARY == "session_summary"
    assert VaultKind.TRANSCRIPT_CHUNK == "transcript_chunk"
```

Change `test_vault_kind_revalidates_stored_text` (line 69) to:

```python
    assert VaultKind(VaultKind.TRANSCRIPT_CHUNK.value) is VaultKind.TRANSCRIPT_CHUNK
```

Change `test_vault_kind_membership_is_exhaustive` (lines 72-79) to:

```python
def test_vault_kind_membership_is_exhaustive() -> None:
    assert {kind.value for kind in VaultKind} == {
        "skill",
        "prompt",
        "preference",
        "session_summary",
        "transcript_chunk",
    }
```

In `backend/tests/db/test_vault_store.py`, replace the three `VaultKind.RUN_RECORD` references (lines 233, 238, 245) with `VaultKind.TRANSCRIPT_CHUNK`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/db/test_types.py tests/db/test_vault_store.py -v`
Expected: FAIL — `AttributeError: SESSION_SUMMARY` / `TRANSCRIPT_CHUNK` (or import-level enum errors).

- [ ] **Step 3: Rename the enum members**

In `backend/src/octave/db/types.py`, replace lines 56-57:

```python
    SESSION_SUMMARY = "session_summary"
    TRANSCRIPT_CHUNK = "transcript_chunk"
```

In `backend/src/octave/db/models/vault.py`, replace the docstring at lines 29-30:

```python
    """One vault entry — see ``VaultKind``: skill | prompt | preference |
    session_summary | transcript_chunk."""
```

- [ ] **Step 4: Run the full db test suite**

Run: `cd backend && uv run pytest tests/db -v`
Expected: all PASS (no other code referenced the old members — verified by grep: `grep -rn "RUN_SUMMARY\|RUN_RECORD\|run_summary\|run_record" src tests` returns nothing under `backend/`).

- [ ] **Step 5: Lint, type-check, commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/octave/db/types.py backend/src/octave/db/models/vault.py backend/tests/db/test_types.py backend/tests/db/test_vault_store.py .agents/specs/2026-09-29-agent-result-collection-design.md
git commit -m "refactor(db): rename run_summary/run_record vault kinds to session scope"
```

(Design doc rides this commit — architect mode could not shell.)

---

### Task 2: `TranscriptDigest` protocol + `HeadTailDigest` (pure logic, no DB)

**Files:**
- Create: `backend/src/octave/agent/summaries.py`
- Create: `backend/tests/agent/test_summaries.py`

- [ ] **Step 1: Write the failing digest tests**

Create `backend/tests/agent/test_summaries.py`:

```python
"""SessionSummarizer + digest strategies (design spec 2026-09-29, issue #28).

Digest tests are pure (synthetic Events, no DB); summarizer tests (Task 3+)
use the vec-enabled env fixture — VaultStore.upsert(embedding=None) calls
remove_vector, so the vec0 table must exist. The import header grows per
task; ruff's F401 gate runs at every commit, so import nothing early.
"""

from octave.db.models import Event
from octave.db.types import EventKind

DIM = 4


def _event(seq: int, kind: EventKind, content: str, author: str | None = None) -> Event:
    """Transient ORM row — digest rendering never touches the DB."""
    return Event(
        id=f"e_{seq}",
        session_id="s_1",
        seq=seq,
        kind=str(kind),
        author_participant_id=author,
        payload={"content": content},
    )


def _labels() -> dict[str, str]:
    return {"p_u1": "Alice", "p_a1": "Echo"}


async def test_digest_renders_whole_transcript_under_budget() -> None:
    from octave.agent.summaries import HeadTailDigest, SummaryContext

    events = [
        _event(1, EventKind.USER_MESSAGE, "hello", "p_u1"),
        _event(2, EventKind.ASSISTANT_MESSAGE, "hi there", "p_a1"),
    ]
    digest = await HeadTailDigest(head_events=2, tail_events=2).digest(
        SummaryContext(session_id="s_1", events=events, labels=_labels())
    )
    assert digest == "User: hello\nEcho: hi there"


async def test_digest_omits_middle_over_budget() -> None:
    from octave.agent.summaries import HeadTailDigest, SummaryContext

    events = [_event(i, EventKind.USER_MESSAGE, f"m{i}", "p_u1") for i in range(1, 8)]
    digest = await HeadTailDigest(head_events=2, tail_events=2).digest(
        SummaryContext(session_id="s_1", events=events, labels=_labels())
    )
    lines = digest.split("\n")
    assert lines[0] == "User: m1"
    assert lines[1] == "User: m2"
    assert lines[2] == "… 3 earlier events omitted …"
    assert lines[-2] == "User: m6"
    assert lines[-1] == "User: m7"


async def test_digest_renders_system_and_unknown_author() -> None:
    from octave.agent.summaries import HeadTailDigest, SummaryContext

    events = [
        _event(1, EventKind.SYSTEM, "Agent turn failed: boom"),
        _event(2, EventKind.ASSISTANT_MESSAGE, "orphan reply", "p_gone"),
    ]
    digest = await HeadTailDigest().digest(
        SummaryContext(session_id="s_1", events=events, labels=_labels())
    )
    assert digest == "System: Agent turn failed: boom\nAssistant: orphan reply"


async def test_digest_tool_events_are_compact_one_liners() -> None:
    from octave.agent.summaries import HeadTailDigest, SummaryContext

    call = _event(1, EventKind.TOOL_CALL, "")
    call.payload = {"tool_name": "calendar.list", "arguments": '{"month": "may"}'}
    result = _event(2, EventKind.TOOL_RESULT, "")
    result.payload = {"content": "x" * 500}
    digest = await HeadTailDigest(tool_line_chars=50).digest(
        SummaryContext(session_id="s_1", events=[call, result], labels=_labels())
    )
    lines = digest.split("\n")
    assert lines[0].startswith("Tool call: calendar.list")
    assert lines[1].startswith("Tool result: xxx")
    assert len(lines[1]) <= 50
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/agent/test_summaries.py -v`
Expected: FAIL — `ModuleNotFoundError: octave.agent.summaries` (or import errors on collection).

- [ ] **Step 3: Implement the digest layer**

Create `backend/src/octave/agent/summaries.py`:

```python
"""Session summaries: derived, cached, LLM-written session overviews (issue #28).

The transcript (``events``) is the canonical record; this module produces the
Tier-1 headline — one summary per session covering what was done and the end
result — cached as a ``VaultKind.SESSION_SUMMARY`` vault item under the
deterministic id ``session_summary:<session_id>``. Follows the VaultStore
convention: constructed with the caller's AsyncSession, never commits. The
LLM call happens before any DB write; generation is out-of-band, never
inside ``MessageRouter.deliver()``.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from octave.db.models import Event
from octave.db.types import EventKind

__all__ = ["HeadTailDigest", "SummaryContext", "TranscriptDigest"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SummaryContext:
    """Material handed to a digest strategy. ``events`` is the full
    transcript, already read; strategies may consult further sources through
    their own seams — ctx is the baseline, not a leash."""

    session_id: str
    events: list[Event]
    labels: dict[str, str]  # participant_id -> display label


class TranscriptDigest(Protocol):
    """Produces the model-facing material for one summary."""

    async def digest(self, ctx: SummaryContext) -> str: ...


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _render_line(event: Event, labels: dict[str, str], tool_line_chars: int) -> str:
    """One transcript entry -> one digest line."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    kind = event.kind
    if kind == EventKind.USER_MESSAGE:
        return f"User: {payload.get('content', '')}"
    if kind == EventKind.ASSISTANT_MESSAGE:
        label = labels.get(event.author_participant_id or "", "Assistant")
        return f"{label}: {payload.get('content', '')}"
    if kind == EventKind.SYSTEM:
        return f"System: {payload.get('content', '')}"
    if kind == EventKind.TOOL_CALL:
        name = payload.get("tool_name") or payload.get("name") or "tool"
        arguments = payload.get("arguments", "")
        return _truncate(f"Tool call: {name} {arguments}", tool_line_chars)
    # TOOL_RESULT and any future kind: content-ish, compact.
    content = payload.get("content") or payload.get("result") or ""
    return _truncate(f"Tool result: {content}", tool_line_chars)


class HeadTailDigest:
    """Default strategy. Whole transcript when
    ``len(events) <= head_events + tail_events``; else first ``head_events``
    + last ``tail_events`` with a ``… N earlier events omitted …`` marker
    between them (N = number omitted)."""

    def __init__(
        self, *, head_events: int = 10, tail_events: int = 40, tool_line_chars: int = 200
    ) -> None:
        self._head_events = head_events
        self._tail_events = tail_events
        self._tool_line_chars = tool_line_chars

    async def digest(self, ctx: SummaryContext) -> str:
        events = ctx.events
        if len(events) <= self._head_events + self._tail_events:
            lines = [
                _render_line(e, ctx.labels, self._tool_line_chars) for e in events
            ]
        else:
            head = events[: self._head_events]
            tail = events[-self._tail_events :]
            omitted = len(events) - len(head) - len(tail)
            lines = [
                _render_line(e, ctx.labels, self._tool_line_chars) for e in head
            ]
            lines.append(f"… {omitted} earlier events omitted …")
            lines.extend(
                _render_line(e, ctx.labels, self._tool_line_chars) for e in tail
            )
        return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_summaries.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/octave/agent/summaries.py backend/tests/agent/test_summaries.py
git commit -m "feat(agent): add TranscriptDigest seam with HeadTailDigest default"
```

---

### Task 3: `SummaryError`, `SessionSummary`, `peek`, and the DB env fixture

**Files:**
- Modify: `backend/src/octave/agent/errors.py` (add after `DeciderChoiceError`)
- Modify: `backend/src/octave/agent/summaries.py`
- Modify: `backend/tests/agent/test_summaries.py` (append fixture + helpers + tests)

- [ ] **Step 1: Add the error**

In `backend/src/octave/agent/errors.py`, add `"SummaryError",` to `__all__` (alphabetical, after `"SessionNotFoundError",`) and append the class after `DeciderChoiceError` (line 77):

```python
class SummaryError(AgentError):
    """Summary generation produced empty/whitespace content (design spec
    2026-09-29). Never persist empty summaries."""
```

- [ ] **Step 2: Write the failing peek tests**

Replace the file's import header with the full block for Tasks 3–6:

```python
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from octave.agent.summaries import SessionSummarizer
from octave.db.config import DbConfig
from octave.db.models import Agent, Base, Event, Participant, Session, User, VaultItem
from octave.db.event_store import EventStore
from octave.db.sqlite_adapter import SqliteVecAdapter
from octave.db.types import ExplicitModelBinding, EventKind
from octave.inference.types import CompletionResult
```

Then append to `backend/tests/agent/test_summaries.py` (the code block below continues into the existing tests):

```python
_TAG_BINDING = {"kind": "tag", "tag": "quick"}


@pytest_asyncio.fixture
async def env(
    tmp_path: Path,
) -> AsyncIterator[tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]]:
    """Vec-enabled DB with seeded user/agent/session/participants. Mirrors
    tests/db/test_vault_store.py's env: VaultStore.upsert(embedding=None)
    calls remove_vector, so the vec0 table must exist."""
    config = DbConfig(
        adapter="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'summaries.db'}",
        embedding_dim=DIM,
    )
    adapter = SqliteVecAdapter(config)
    engine: AsyncEngine = adapter.make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await adapter.ensure_vector_store(conn)
    factory = adapter.make_session_factory(engine)
    async with factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(Agent(id="a_1", name="Echo", model_binding=_TAG_BINDING))
        session.add(Session(id="s_1", created_by_user_id="u_1", status="active"))
        session.add(Participant(id="p_u1", user_id="u_1", label="Alice"))
        session.add(Participant(id="p_a1", agent_id="a_1", label="Echo"))
        await session.commit()
    yield adapter, factory
    await engine.dispose()


def _explicit_binding() -> ExplicitModelBinding:
    return ExplicitModelBinding(kind="explicit", adapter="fake", model="summarizer-1")


class ScriptedAdapter:
    """Minimal in-test adapter: serves queued results, records requests.
    (tests/agent/fakes.py's ScriptedAdapter needs AdapterConfig; this one is
    duck-typed for complete() only.)"""

    def __init__(self, results: list[CompletionResult | Exception]) -> None:
        self._results = list(results)
        self.complete_calls = []

    async def complete(self, request):  # noqa: ANN001 — duck-typed seam
        self.complete_calls.append(request)
        if not self._results:
            raise AssertionError("ScriptedAdapter queue exhausted")
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _summarizer(
    session: AsyncSession,
    db_adapter: SqliteVecAdapter,
    scripted: ScriptedAdapter,
    **overrides,  # noqa: ANN001
) -> SessionSummarizer:
    return SessionSummarizer(
        session=session,
        db_adapter=db_adapter,
        binding=_explicit_binding(),
        adapter_for=lambda name: scripted,
        **overrides,
    )


async def _seed_transcript(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        store = EventStore(session)
        await store.append(
            "s_1",
            EventKind.USER_MESSAGE,
            author_participant_id="p_u1",
            payload={"content": "hello"},
        )
        await store.append(
            "s_1",
            EventKind.ASSISTANT_MESSAGE,
            author_participant_id="p_a1",
            payload={"content": "hi there"},
        )
        await session.commit()


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="summarizer-1", finish_reason="stop")


async def test_peek_returns_none_when_absent(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    async with factory() as session:
        summarizer = _summarizer(session, adapter, ScriptedAdapter([]))
        assert await summarizer.peek("s_1") is None


async def test_peek_reads_item_written_by_vault_store(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    from octave.db.vault_store import VaultStore

    adapter, factory = env
    async with factory() as session:
        await VaultStore(adapter, session).upsert(
            item_id="session_summary:s_1",
            user_id="u_1",
            kind=VaultKind.SESSION_SUMMARY,
            name="Greeting",
            content="Alice greeted; Echo replied.",
            meta={"session_id": "s_1", "covered_seq": 2, "model_name": "summarizer-1"},
        )
        await session.commit()
    async with factory() as session:
        summarizer = _summarizer(session, adapter, ScriptedAdapter([]))
        summary = await summarizer.peek("s_1")
    assert summary is not None
    assert summary.title == "Greeting"
    assert summary.content == "Alice greeted; Echo replied."
    assert summary.covered_seq == 2
    assert summary.owner_user_id == "u_1"
    assert summary.model_name == "summarizer-1"
    assert summary.session_id == "s_1"


async def test_peek_treats_malformed_meta_as_missing(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    async with factory() as session:
        session.add(
            VaultItem(
                id="session_summary:s_1",
                user_id="u_1",
                kind="session_summary",
                name="x",
                content="y",
                meta={"nope": True},
            )
        )
        await session.commit()
    async with factory() as session:
        summarizer = _summarizer(session, adapter, ScriptedAdapter([]))
        assert await summarizer.peek("s_1") is None
```

(The header replacement above already includes `VaultItem`.)

- [ ] **Step 3: Run to verify failure**

Run: `cd backend && uv run pytest tests/agent/test_summaries.py -v`
Expected: FAIL — `ImportError: cannot import name 'SessionSummarizer'`.

- [ ] **Step 4: Implement `SessionSummary`, `peek`, and the constructor**

Extend `backend/src/octave/agent/summaries.py`. Add imports (merge with existing; Task 4 adds the rest — import nothing early, ruff F401 gates every commit):

```python
from collections.abc import Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from octave.db.adapter import DbAdapter
from octave.db.event_store import EventStore
from octave.db.models import VaultItem
from octave.db.vault_store import VaultStore
from octave.db.types import ModelBinding
from octave.inference.adapter import InferenceAdapter
```

Update `__all__`:

```python
__all__ = [
    "HeadTailDigest",
    "SessionSummarizer",
    "SessionSummary",
    "SummaryContext",
    "TranscriptDigest",
]
```

Add the dataclass and helpers:

```python
@dataclass(frozen=True)
class SessionSummary:
    """One session's derived headline. ``covered_seq`` is the transcript
    seq at generation time — freshness is computed, never stored."""

    session_id: str
    owner_user_id: str
    title: str
    content: str
    covered_seq: int
    model_name: str | None
    generated_at: datetime


def _item_id(session_id: str) -> str:
    """Deterministic vault id: regeneration is an idempotent replacement."""
    return f"session_summary:{session_id}"


def _to_summary(item: VaultItem, session_id: str) -> SessionSummary | None:
    """Lenient by design (reporting surface): malformed cached meta is
    treated as missing, mirroring the registry's _parse_assignments rule."""
    meta = item.meta if isinstance(item.meta, dict) else {}
    covered = meta.get("covered_seq")
    if not isinstance(covered, int):
        return None
    model_name = meta.get("model_name")
    return SessionSummary(
        session_id=session_id,
        owner_user_id=item.user_id,
        title=item.name,
        content=item.content,
        covered_seq=covered,
        model_name=model_name if isinstance(model_name, str) else None,
        generated_at=item.updated_at,
    )
```


Add the class:

```python
class SessionSummarizer:
    """Generate + cache one LLM summary per session. Never commits; callers
    own transaction boundaries (``octave.db.deps``)."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        db_adapter: DbAdapter,
        binding: ModelBinding,
        adapter_for: Callable[[str | None], InferenceAdapter],
        tag_lookup: Callable[[str], str | None] | None = None,
        digest: TranscriptDigest | None = None,
    ) -> None:
        self._session = session
        self._binding = binding
        self._adapter_for = adapter_for
        self._tag_lookup = tag_lookup
        self._digest = digest if digest is not None else HeadTailDigest()
        self._events = EventStore(session)
        self._vault = VaultStore(db_adapter, session)

    async def peek(self, session_id: str) -> SessionSummary | None:
        """Cached summary only; never calls the adapter. The cheap path for
        list views."""
        item = await self._vault.get(_item_id(session_id))
        if item is None:
            return None
        return _to_summary(item, session_id)
```

(`datetime`, `Callable`, and `ModelBinding` are already in the import block above.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_summaries.py -v`
Expected: 7 PASS.

- [ ] **Step 6: Lint, type-check, commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/octave/agent/errors.py backend/src/octave/agent/summaries.py backend/tests/agent/test_summaries.py
git commit -m "feat(agent): SessionSummarizer skeleton — SummaryError, SessionSummary, peek"
```

---

### Task 4: `collect()` — not-found, empty transcript, generate + persist happy path

**Files:**
- Modify: `backend/src/octave/agent/summaries.py`
- Modify: `backend/tests/agent/test_summaries.py`

- [ ] **Step 1: Write the failing collect tests**

Add `import pytest` to the file's import block (third-party group, before `pytest_asyncio`). Then append to `backend/tests/agent/test_summaries.py`:

```python
async def test_collect_raises_session_not_found(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    from octave.agent.errors import SessionNotFoundError

    adapter, factory = env
    async with factory() as session:
        summarizer = _summarizer(session, adapter, ScriptedAdapter([]))
        with pytest.raises(SessionNotFoundError):
            await summarizer.collect("s_missing")


async def test_collect_empty_transcript_returns_none(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    scripted = ScriptedAdapter([])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        assert await summarizer.collect("s_1") is None
    assert scripted.complete_calls == []


async def test_collect_generates_persists_and_returns(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter([_completion("Title: Greeting\nAlice greeted; Echo replied.")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        summary = await summarizer.collect("s_1")
        await session.commit()
    assert summary is not None
    assert summary.title == "Greeting"
    assert summary.content == "Alice greeted; Echo replied."
    assert summary.covered_seq == 2
    assert summary.owner_user_id == "u_1"
    assert summary.model_name == "summarizer-1"

    # The vault item landed with deterministic id, kind, meta, no embedding.
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        item = await VaultStore(adapter, session).get("session_summary:s_1")
    assert item is not None
    assert item.kind == "session_summary"
    assert item.user_id == "u_1"
    assert item.meta == {
        "session_id": "s_1",
        "covered_seq": 2,
        "model_name": "summarizer-1",
    }
    assert item.embedding is None

    # One completion: system instruction + digest with participant labels.
    assert len(scripted.complete_calls) == 1
    request = scripted.complete_calls[0]
    assert request.model == "summarizer-1"
    assert request.messages[0].role == "system"
    assert "Title:" in request.messages[0].content
    assert request.messages[1].content == "User: hello\nEcho: hi there"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/agent/test_summaries.py -v`
Expected: new collect tests FAIL — `AttributeError: 'SessionSummarizer' object has no attribute 'collect'`.

- [ ] **Step 3: Implement `collect` (no cache check yet — Task 5 adds it)**

Add to `backend/src/octave/agent/summaries.py` (module level):

```python
_SYSTEM_INSTRUCTION = (
    "You are writing a recap of an agent session for the user who owns it. "
    "Summarize what was done and state the end result. "
    "The first line must be exactly 'Title: <short title>' followed by the "
    "recap body."
)


def _fallback_title(session_row: Session, events: list[Event]) -> str:
    """session.title → first user message (truncated) → Session <id[:8]>."""
    if session_row.title:
        return session_row.title
    for event in events:
        if event.kind == EventKind.USER_MESSAGE:
            content = str(event.payload.get("content", "")).strip()
            if content:
                return content[:60]
    return f"Session {session_row.id[:8]}"


def _parse_summary(
    text: str, *, session_row: Session, events: list[Event]
) -> tuple[str, str]:
    """Split the model's Title line from the body; fall back when the
    contract is ignored. Malformed output never fails a generation."""
    head, sep, rest = text.partition("\n")
    if sep and head.strip().lower().startswith("title:"):
        title = head.strip()[len("Title:"):].strip()
        body = rest.strip()
        if title and body:
            return title, body
    return _fallback_title(session_row, events), text
```

Add the method to `SessionSummarizer`:

```python
    async def collect(
        self, session_id: str, *, force: bool = False
    ) -> SessionSummary | None:
        """Fresh cached summary, or generate + persist. Empty transcript →
        None (no LLM call, no item written)."""
        session_row = await self._session.get(Session, session_id)
        if session_row is None:
            raise SessionNotFoundError(session_id)
        events = await self._events.read(session_id)
        if not events:
            return None
        seq_max = events[-1].seq
        # Task 5 inserts the cache check here.
        return await self._generate(session_row, events, seq_max)

    async def _generate(
        self, session_row: Session, events: list[Event], seq_max: int
    ) -> SessionSummary:
        labels = await self._labels(events)
        digest_text = await self._digest.digest(
            SummaryContext(session_id=session_row.id, events=events, labels=labels)
        )
        resolved = resolve_model(self._binding, tag_lookup=self._tag_lookup)
        adapter = self._adapter_for(resolved.adapter)
        result = await adapter.complete(
            CompletionRequest(
                model=resolved.model,
                messages=[
                    Message(role="system", content=_SYSTEM_INSTRUCTION),
                    Message(role="user", content=digest_text),
                ],
            )
        )
        text = result.text.strip()
        if not text:
            raise SummaryError(
                f"summary generation for {session_row.id} returned empty content"
            )
        title, body = _parse_summary(text, session_row=session_row, events=events)
        item = await self._vault.upsert(
            item_id=_item_id(session_row.id),
            user_id=session_row.created_by_user_id,
            kind=VaultKind.SESSION_SUMMARY,
            name=title,
            content=body,
            meta={
                "session_id": session_row.id,
                "covered_seq": seq_max,
                "model_name": result.model,
            },
        )
        summary = _to_summary(item, session_row.id)
        assert summary is not None  # written by us; meta is well-formed
        return summary

    async def _labels(self, events: list[Event]) -> dict[str, str]:
        author_ids = {
            event.author_participant_id for event in events if event.author_participant_id
        }
        if not author_ids:
            return {}
        rows = await self._session.execute(
            select(Participant.id, Participant.label).where(
                Participant.id.in_(author_ids)
            )
        )
        return {participant_id: label for participant_id, label in rows.all()}
```

Add imports: `select` from `sqlalchemy`; `Participant`, `Session` to the `octave.db.models` import; `from octave.agent.errors import SessionNotFoundError, SummaryError`; `from octave.agent.instances import resolve_model`; `CompletionRequest`, `Message` to the `octave.inference.types` import; `VaultKind` to the `octave.db.types` import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_summaries.py -v`
Expected: 10 PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/octave/agent/summaries.py backend/tests/agent/test_summaries.py
git commit -m "feat(agent): SessionSummarizer.collect generates and persists summaries"
```

---

### Task 5: Cache freshness, staleness regeneration, `force`, never-commits

**Files:**
- Modify: `backend/src/octave/agent/summaries.py` (`collect` — replace the `# Task 5` comment with the cache check)
- Modify: `backend/tests/agent/test_summaries.py`

- [ ] **Step 1: Write the failing cache tests**

Append to `backend/tests/agent/test_summaries.py`:

```python
async def test_collect_returns_cached_when_fresh(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter([_completion("Title: Greeting\nfirst.")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        await summarizer.collect("s_1")
        await session.commit()
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)  # queue empty
        summary = await summarizer.collect("s_1")
    assert summary is not None
    assert summary.content == "first."
    assert len(scripted.complete_calls) == 1  # no second call


async def test_collect_regenerates_when_transcript_grew(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter(
        [_completion("Title: Greeting\nfirst."), _completion("Title: Later\nsecond.")]
    )
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        await summarizer.collect("s_1")
        await session.commit()
    async with factory() as session:
        await EventStore(session).append(
            "s_1",
            EventKind.USER_MESSAGE,
            author_participant_id="p_u1",
            payload={"content": "and then?"},
        )
        await session.commit()
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        summary = await summarizer.collect("s_1")
        await session.commit()
    assert summary is not None
    assert summary.content == "second."
    assert summary.covered_seq == 3
    assert len(scripted.complete_calls) == 2
    async with factory() as session:
        fresh = _summarizer(session, adapter, ScriptedAdapter([]))
        peeked = await fresh.peek("s_1")
    assert peeked is not None
    assert peeked.content == "second."  # same deterministic item id


async def test_collect_force_regenerates_even_when_fresh(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter(
        [_completion("Title: Greeting\nfirst."), _completion("Title: Greeting\nrewritten.")]
    )
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        await summarizer.collect("s_1")
        second = await summarizer.collect("s_1", force=True)
        await session.commit()
    assert second is not None
    assert second.content == "rewritten."
    assert len(scripted.complete_calls) == 2


async def test_collect_never_commits(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter([_completion("Title: Greeting\nbody.")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        await summarizer.collect("s_1")
        await session.rollback()
    async with factory() as session:
        summarizer = _summarizer(session, adapter, ScriptedAdapter([]))
        assert await summarizer.peek("s_1") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/agent/test_summaries.py -v`
Expected: cache/staleness/force tests FAIL (every `collect` regenerates today; the empty queue raises `AssertionError` in the cached test).

- [ ] **Step 3: Insert the cache check**

In `backend/src/octave/agent/summaries.py`, replace the line `# Task 5 inserts the cache check here.` in `collect` with:

```python
        if not force:
            item = await self._vault.get(_item_id(session_id))
            if item is not None:
                cached = _to_summary(item, session_id)
                if cached is not None and cached.covered_seq == seq_max:
                    logger.debug("session summary cache hit | session=%s", session_id)
                    return cached
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_summaries.py -v`
Expected: 14 PASS (concurrent-collect last-write-wins needs no code: `upsert` is full replacement; covered by the regenerate test).

- [ ] **Step 5: Lint, type-check, commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/octave/agent/summaries.py backend/tests/agent/test_summaries.py
git commit -m "feat(agent): computed staleness + force for session summaries"
```

---

### Task 6: Error policy — adapter failure, empty completion, binding resolution

**Files:**
- Modify: `backend/tests/agent/test_summaries.py` (implementation from Tasks 3–5 should already satisfy these; they pin the contract)

- [ ] **Step 1: Write the contract tests**

Append to `backend/tests/agent/test_summaries.py`:

```python
async def test_adapter_error_propagates_without_partial_write(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    from octave.inference.errors import AdapterConnectionError

    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter([AdapterConnectionError("engine down")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        with pytest.raises(AdapterConnectionError):
            await summarizer.collect("s_1")
        await session.rollback()
    async with factory() as session:
        peeked = await _summarizer(session, adapter, ScriptedAdapter([])).peek("s_1")
    assert peeked is None


async def test_empty_completion_raises_summary_error(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    from octave.agent.errors import SummaryError

    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter([_completion("   \n  ")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        with pytest.raises(SummaryError):
            await summarizer.collect("s_1")
        await session.rollback()
    async with factory() as session:
        peeked = await _summarizer(session, adapter, ScriptedAdapter([])).peek("s_1")
    assert peeked is None


async def test_explicit_binding_passes_adapter_name_to_adapter_for(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    seen: list[str | None] = []
    scripted = ScriptedAdapter([_completion("Title: T\nbody.")])

    def adapter_for(name: str | None) -> ScriptedAdapter:
        seen.append(name)
        return scripted

    async with factory() as session:
        summarizer = SessionSummarizer(
            session=session,
            db_adapter=adapter,
            binding=_explicit_binding(),
            adapter_for=adapter_for,
        )
        await summarizer.collect("s_1")
    assert seen == ["fake"]  # ExplicitModelBinding(adapter="fake")


async def test_tag_binding_without_lookup_fails_loud(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    from octave.agent.errors import ModelBindingError

    adapter, factory = env
    await _seed_transcript(factory)
    async with factory() as session:
        summarizer = SessionSummarizer(
            session=session,
            db_adapter=adapter,
            binding=TagModelBinding(kind="tag", tag="quick"),
            adapter_for=lambda name: ScriptedAdapter([]),
        )
        with pytest.raises(ModelBindingError):
            await summarizer.collect("s_1")
```

Add `TagModelBinding` to the `octave.db.types` import in the test file.

- [ ] **Step 2: Run tests**

Run: `cd backend && uv run pytest tests/agent/test_summaries.py -v`
Expected: 18 PASS. If any fail, fix the implementation in `summaries.py` to match (do not weaken the test) — the Tasks 3–5 code is intended to satisfy these as written.

- [ ] **Step 3: Commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/tests/agent/test_summaries.py
git commit -m "test(agent): pin SessionSummarizer error and binding-resolution contracts"
```

---

- [ ] **Step 4: Title fallback chain tests**

Append to `backend/tests/agent/test_summaries.py`:

```python
async def test_title_falls_back_to_session_title(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    async with factory() as session:
        session_row = await session.get(Session, "s_1")
        session_row.title = "Weekly review"
        await session.commit()
    scripted = ScriptedAdapter([_completion("no title line here\nBody prose.")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        summary = await summarizer.collect("s_1")
    assert summary is not None
    assert summary.title == "Weekly review"
    assert summary.content == "no title line here\nBody prose."


async def test_title_falls_back_to_first_user_message(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter([_completion("Body only, no title contract.")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        summary = await summarizer.collect("s_1")
    assert summary is not None
    assert summary.title == "hello"  # first user message content


async def test_title_falls_back_to_session_id_when_no_user_message(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    async with factory() as session:
        await EventStore(session).append(
            "s_1", EventKind.SYSTEM, payload={"content": "system only"}
        )
        await session.commit()
    scripted = ScriptedAdapter([_completion("Body.")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        summary = await summarizer.collect("s_1")
    assert summary is not None
    assert summary.title == "Session s_1"  # f"Session {id[:8]}" — "s_1" is 3 chars
```

- [ ] **Step 5: Run tests**

Run: `cd backend && uv run pytest tests/agent/test_summaries.py -v`
Expected: 21 PASS. If the third fallback test fails on the id slice (`"s_1"[:8] == "s_1"` → `"Session s_1"`), the implementation is correct — assert the actual `f"Session {'s_1'[:8]}"`.

- [ ] **Step 6: Commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/tests/agent/test_summaries.py
git commit -m "test(agent): pin session-summary title fallback chain"
```

---

### Task 7: Package exports

**Files:**
- Modify: `backend/src/octave/agent/__init__.py`
- Modify: `backend/tests/agent/test_package.py:29-66`

- [ ] **Step 1: Update the failing package test**

In `backend/tests/agent/test_package.py`, add to the tuple in `test_public_names_are_exported` (alphabetical positions): `"HeadTailDigest", "SessionSummarizer", "SessionSummary", "SummaryContext", "SummaryError", "TranscriptDigest"`.

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/agent/test_package.py -v`
Expected: FAIL — `AssertionError: HeadTailDigest` (etc.).

- [ ] **Step 3: Export from the package**

In `backend/src/octave/agent/__init__.py`: add `SummaryError,` to the `from octave.agent.errors import (...)` block, and add after the router import block:

```python
from octave.agent.summaries import (
    HeadTailDigest,
    SessionSummarizer,
    SessionSummary,
    SummaryContext,
    TranscriptDigest,
)
```

Add the five names + `"SummaryError"` to `__all__` (alphabetical). Update the module docstring line: `Result collection (#4) and` → `scheduling (#6) remain.` becomes `Scheduling (#6) remains.` (summaries shipped here).

- [ ] **Step 4: Run the full agent + db suites**

Run: `cd backend && uv run pytest tests/agent tests/db -v`
Expected: all PASS (quarantine guard unaffected — no `openai`/`mcp` imports in `summaries.py`).

- [ ] **Step 5: Lint, type-check, commit**

```bash
cd backend && uv run ruff check src tests && uv run mypy src
git add backend/src/octave/agent/__init__.py backend/tests/agent/test_package.py
git commit -m "feat(agent): export session-summarizer surface from octave.agent"
```

---

### Task 8: ADR + TODO bookkeeping

**Files:**
- Modify: `.agents/memory/decisions.md` (new entry + amendment)
- Modify: `docs/TODO.md:110`

- [ ] **Step 1: Add the ADR entry**

Append to the end of `## Decisions` in `.agents/memory/decisions.md`:

```markdown
### 2026-09-29 — Session-Scoped Summaries; Vault Kind Renames

**Context:** Issue #28 (Agent Manager #4, result collection). The transcript
(`events`, shipped via #27) already persists every reply; `run_summary` /
`run_record` (2026-09-20) were named before any writer existed. Brainstorming
settled the product shape: the user-side deliverable is one LLM-written
summary per session (what was done + end result) serving both the result
viewer and CM #5's Tier-1 archival. The "run" prefix collided with
whole-session intuition; with zero rows written, renaming is an enum change,
not a migration.

**Options Considered:** 1) per-turn summaries (N LLM calls per exchange,
fragmented narrative); 2) deterministic compilation (rejected by product
owner); 3) session-scoped LLM summary, cached as a vault item, refreshed
when `covered_seq < seq_max`.

**Decision:** Option 3. `SessionSummarizer` (`octave.agent.summaries`)
generates out-of-band — never inside `MessageRouter.deliver()`; the LLM call
precedes any DB write. Persisted via `VaultStore` as `VaultKind.SESSION_SUMMARY`
under deterministic id `session_summary:<session_id>`, embedding NULL (CM #5
attaches embeddings to the same item — one summary, two consumers). Summarizer
model chosen via the shipped `ModelBinding` union + `resolve_model`. Material
policy behind a `TranscriptDigest` protocol (`HeadTailDigest` default).
Renamed `RUN_SUMMARY → SESSION_SUMMARY`, `RUN_RECORD → TRANSCRIPT_CHUNK`.

**Rationale:** events = canonical recording (replay, turn input); vault =
derived search index (embeddable prose). Chunking stays CM #5's policy call —
never one vault row per event. The rename door closes the moment #28 writes
items, per the 2026-09-21 one-way-door precedent.

**Consequences:** The vault gains its first writer from the agent plane.
`TurnRecord` remains CM #5's verbatim-chunking seam, unchanged. Summary
staleness is computed on read; no invalidation machinery. Follow-up issues:
search-augmented digest, map-reduce digest, result-viewer UI wiring.
```

- [ ] **Step 2: Amend the 2026-09-20 entry**

In `.agents/memory/decisions.md`, append to the end of the `2026-09-20 — Context Vault Data Model` entry (after its **Consequences** paragraph):

```markdown
**Amendment (2026-09-29):** Kinds renamed before any row was written —
`run_summary` → `session_summary`, `run_record` → `transcript_chunk`; the
summary is session-scoped (whole session, all agents), the record is the
`events` table itself. See the 2026-09-29 ADR.
```

- [ ] **Step 3: Mark Agent Manager #4 done**

In `docs/TODO.md`, replace line 110:

```markdown
- [x] 4. Create agent result collection (capture agent outputs and make them queryable) — PR #108 (session-scoped `SessionSummarizer`: LLM-written `session_summary` vault items cached over the transcript, `TranscriptDigest` seam; vault kinds renamed `session_summary`/`transcript_chunk`)
```

- [ ] **Step 4: Commit**

```bash
git add .agents/memory/decisions.md docs/TODO.md
git commit -m "docs: ADR for session-scoped summaries and vault kind renames"
```

---

### Task 9: Full verification

- [ ] **Step 1: Full backend suite**

Run: `cd backend && uv run pytest -v`
Expected: all PASS.

- [ ] **Step 2: Lint + types on the whole tree**

Run: `cd backend && uv run ruff check src tests && uv run mypy src`
Expected: no errors.

- [ ] **Step 3: Stale-reference sweep**

Run: `grep -rn "run_summary\|run_record\|RUN_SUMMARY\|RUN_RECORD" backend/src backend/tests`
Expected: no matches (docs/specs may reference old names historically; code must not).

- [ ] **Step 4: Push to the PR branch**

```bash
git push origin feature/agent-result-collection
```
