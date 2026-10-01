# Agent Context Lifecycle (Archival & Embedding) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `octave.context` — a pull-only `ContextArchiver` that reconstructs agent turn brackets from the transcript, writes verbatim `transcript_chunk` vault items, and embeds Tier-1 `session_summary` + Tier-2 chunks into the vector store so future linked runs can query them.

**Architecture:** New package `octave/context/` (CM service layer) importing `octave.db` + `octave.inference` only — never `octave.agent`. Pure modules for bracket reconstruction and chunk planning; one orchestrator (`ContextArchiver.archive(session_id)`) that embeds via the `ModelBinding`/`adapter_for` seam and writes through `VaultStore` (never commits). Idempotence via deterministic item ids.

**Tech Stack:** Python 3.12, SQLAlchemy async, SQLite+vec0 (via shipped `SqliteVecAdapter`), pytest/pytest-asyncio, `uv`.

**Spec:** [2026-09-30-agent-context-lifecycle-design.md](./2026-09-30-agent-context-lifecycle-design.md) (issue #35, PR #114, branch `feature/agent-context-lifecycle`).

**Deviation note (plan location):** saved to `.agents/specs/` per house convention (user preference overrides the skill default `docs/superpowers/plans/`).

**Working directory for ALL commands:** `backend/` (i.e. `cd backend` first).

---

## Conventions from the shipped codebase (read before starting)

- **Never commit** inside library code; tests own transaction boundaries (`octave.db.deps` rule, see [`vault_store.py`](../../backend/src/octave/db/vault_store.py)).
- `VaultStore.upsert` is **full replacement** — omitted optionals are written as such. To keep fields, re-supply them (read-modify-write).
- `VaultKind` is an app-validated `StrEnum` in [`octave.db.types`](../../backend/src/octave/db/types.py); `TRANSCRIPT_CHUNK` and `SESSION_SUMMARY` already exist.
- `Event` rows carry `author_participant_id`; agents get their `agent_id` by joining `Participant.agent_id`.
- Shared DB fixtures live in [`tests/conftest.py`](../../backend/tests/conftest.py) (`engine`, `session_factory`); the vector-store-enabled pattern is the `env` fixture in [`tests/db/test_vault_store.py`](../../backend/tests/db/test_vault_store.py) — we replicate it locally in `tests/context/conftest.py` because we also seed sessions/agents/participants.
- Gates (run from `backend/`): `uv run pytest -q && uv run ruff check src tests && uv run mypy src`.

---

### Task 1: Package scaffold + CM-local errors + package guards

**Files:**
- Create: `backend/src/octave/context/__init__.py`
- Create: `backend/src/octave/context/errors.py`
- Create: `backend/tests/context/__init__.py` (empty)
- Create: `backend/tests/context/test_package.py`

- [ ] **Step 1: Write the failing package test**

Create `backend/tests/context/__init__.py` (empty file). Create `backend/tests/context/test_package.py`:

```python
"""Public API surface and plane-boundary posture of the context package."""

import ast
from pathlib import Path

import octave.context as context_pkg

SDK_MODULES = {"openai", "mcp"}
BANNED_OCTAVE_MODULES = {"agent"}


def _imported_top_level_octave_modules() -> set[str]:
    names: set[str] = set()
    for path in Path(context_pkg.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")
                if top[0] == "octave" and len(top) > 1:
                    names.add(top[1])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")
                    if top[0] == "octave" and len(top) > 1:
                        names.add(top[1])
    return names


def test_no_sdk_imports() -> None:
    """octave.context composes Octave façades only, never SDKs."""
    assert not _imported_top_level_octave_modules() & SDK_MODULES


def test_no_agent_plane_imports() -> None:
    """The CM plane must not import octave.agent (design spec Decision 9)."""
    assert not _imported_top_level_octave_modules() & BANNED_OCTAVE_MODULES


def test_error_names_are_exported() -> None:
    for name in ("SessionNotFound", "ModelBindingNotResolved"):
        assert hasattr(context_pkg, name), name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/context/test_package.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octave.context'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/octave/context/errors.py`:

```python
"""Context-plane errors (issue #35).

Independent of ``octave.agent.errors``: the plane ban (design spec
Decision 9) forbids importing the agent plane, so names mirror but do not
share types. The composition root maps whichever it catches from its two
components.
"""

__all__ = ["ContextError", "ModelBindingNotResolved", "SessionNotFound"]


class ContextError(Exception):
    """Base for octave.context errors."""


class SessionNotFound(ContextError):
    """No sessions row for the requested id."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"session {session_id!r} not found")
        self.session_id = session_id


class ModelBindingNotResolved(ContextError):
    """Embed binding unresolvable (tag form without a lookup, or tag miss).
    Mirrors the shipped ``ModelBindingError`` fail-loud policy."""
```

Create `backend/src/octave/context/__init__.py`:

```python
"""Context Manager service layer (issue #35).

Archives completed agent runs into the vault for future linked runs to
query. Imports ``octave.db`` + ``octave.inference`` only — never
``octave.agent`` (design spec 2026-09-30, Decision 9).
"""

from octave.context.errors import ModelBindingNotResolved, SessionNotFound

__all__ = [
    "ModelBindingNotResolved",
    "SessionNotFound",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/context/test_package.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/context backend/tests/context
git commit -m "feat(context): scaffold context package with CM-local errors and plane guards"
```

---

### Task 2: Bracket reconstruction (`brackets.py`)

**Files:**
- Create: `backend/src/octave/context/brackets.py`
- Create: `backend/tests/context/test_brackets.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/context/test_brackets.py`:

```python
"""Bracket reconstruction (design spec Decision 3).

Pure tests build Event objects in memory (never flushed — reconstruct
reads only .seq/.kind/.author_participant_id). The router-equivalence
test (Task 5) proves the rule against the shipped MessageRouter.
"""

from octave.context.brackets import TurnBracket, reconstruct_turns
from octave.db.models import Event
from octave.db.types import EventKind

MAP = {"p_a1": "a_1", "p_a2": "a_2"}


def _event(seq: int, kind: EventKind, author: str | None = None) -> Event:
    return Event(
        id=f"e{seq}",
        session_id="s_1",
        seq=seq,
        kind=str(kind),
        author_participant_id=author,
        payload={},
    )


def test_single_turn_bracket() -> None:
    events = [
        _event(1, EventKind.USER_MESSAGE, "p_u1"),
        _event(2, EventKind.TOOL_CALL, "p_a1"),
        _event(3, EventKind.TOOL_RESULT, "p_a1"),
        _event(4, EventKind.ASSISTANT_MESSAGE, "p_a1"),
    ]
    assert reconstruct_turns(events, MAP) == [TurnBracket("a_1", 2, 4)]


def test_contiguous_brackets_same_agent() -> None:
    events = [
        _event(1, EventKind.USER_MESSAGE, "p_u1"),
        _event(2, EventKind.TOOL_CALL, "p_a1"),
        _event(3, EventKind.ASSISTANT_MESSAGE, "p_a1"),
        _event(4, EventKind.TOOL_CALL, "p_a1"),
        _event(5, EventKind.ASSISTANT_MESSAGE, "p_a1"),
    ]
    assert reconstruct_turns(events, MAP) == [
        TurnBracket("a_1", 2, 3),
        TurnBracket("a_1", 4, 5),
    ]


def test_multi_agent_alternation() -> None:
    events = [
        _event(1, EventKind.USER_MESSAGE, "p_u1"),
        _event(2, EventKind.TOOL_CALL, "p_a1"),
        _event(3, EventKind.ASSISTANT_MESSAGE, "p_a1"),
        _event(4, EventKind.TOOL_CALL, "p_a2"),
        _event(5, EventKind.ASSISTANT_MESSAGE, "p_a2"),
    ]
    assert reconstruct_turns(events, MAP) == [
        TurnBracket("a_1", 2, 3),
        TurnBracket("a_2", 4, 5),
    ]


def test_failed_turn_orphans_produce_no_bracket() -> None:
    events = [
        _event(1, EventKind.USER_MESSAGE, "p_u1"),
        _event(2, EventKind.TOOL_CALL, "p_a1"),
        _event(3, EventKind.TOOL_RESULT, "p_a1"),
        _event(4, EventKind.SYSTEM, None),  # failure notice, unauthored
    ]
    assert reconstruct_turns(events, MAP) == []


def test_trailing_unclosed_events_produce_no_bracket() -> None:
    events = [
        _event(1, EventKind.USER_MESSAGE, "p_u1"),
        _event(2, EventKind.TOOL_CALL, "p_a1"),
    ]
    assert reconstruct_turns(events, MAP) == []


def test_anchor_mid_stream_opens_next_region_after_it() -> None:
    events = [
        _event(1, EventKind.USER_MESSAGE, "p_u1"),
        _event(2, EventKind.ASSISTANT_MESSAGE, "p_a1"),
        _event(3, EventKind.USER_MESSAGE, "p_u1"),  # anchor between turns
        _event(4, EventKind.TOOL_CALL, "p_a1"),
        _event(5, EventKind.ASSISTANT_MESSAGE, "p_a1"),
    ]
    assert reconstruct_turns(events, MAP) == [
        TurnBracket("a_1", 2, 2),
        TurnBracket("a_1", 4, 5),
    ]


def test_empty_transcript() -> None:
    assert reconstruct_turns([], MAP) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/context/test_brackets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octave.context.brackets'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/octave/context/brackets.py`:

```python
"""Reconstruct agent turn brackets from a transcript (issue #35).

The router's claim points are ephemeral — ``TurnRecord`` is returned in
``RouteOutcome.turns`` and never persisted. The transcript is the durable
record, and committed transcripts encode brackets exactly (design spec
Decision 3)::

    bracket = ( max(prev_agent_reply_seq, last_non_agent_event_seq), reply_seq ]

Walk seq order: agent-authored events open a region; that agent's
``assistant_message`` closes it. Non-agent events (user messages,
unauthored system notices) are anchors — excluded from every bracket.
A failed turn leaves orphan agent events with no closing reply: no
bracket, matching push semantics (the router emits no ``TurnRecord``
either). A task in ``tests/context/test_brackets.py`` pins this against
the shipped router's output.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from octave.db.models import Event
from octave.db.types import EventKind

__all__ = ["TurnBracket", "reconstruct_turns"]


@dataclass(frozen=True)
class TurnBracket:
    """One completed agent turn: agent-authored events ``seq_start..seq_end``
    inclusive, closed by the agent's reply at ``seq_end``. Deliberately not
    ``octave.agent.router.TurnRecord`` — no ``instance_id`` (archival keys on
    ``(session_id, agent_id, seq_range)``, #25 ADR) and no cross-plane import.
    """

    agent_id: str
    seq_start: int
    seq_end: int


def reconstruct_turns(
    events: Sequence[Event],
    agent_by_participant: Mapping[str, str],
) -> list[TurnBracket]:
    """Pure: seq-ordered events + participant→agent map → brackets.

    ``agent_by_participant`` maps participant id to agent id for agent
    participants only; events authored by anyone else (users, unauthored
    system notices) are anchors.
    """
    brackets: list[TurnBracket] = []
    anchor = 0  # seq of the last event that cannot belong to a bracket
    region_start: int | None = None  # start of the current agent-authored run
    for event in sorted(events, key=lambda e: e.seq):
        agent_id = agent_by_participant.get(event.author_participant_id or "")
        if agent_id is None:
            anchor = event.seq
            region_start = None
            continue
        if region_start is None:
            region_start = anchor + 1
        if event.kind == EventKind.ASSISTANT_MESSAGE:
            brackets.append(
                TurnBracket(
                    agent_id=agent_id, seq_start=region_start, seq_end=event.seq
                )
            )
            anchor = event.seq
            region_start = None
    return brackets
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/context/test_brackets.py -v`
Expected: 7 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/context/brackets.py backend/tests/context/test_brackets.py
git commit -m "feat(context): reconstruct turn brackets from transcript events"
```

---

### Task 3: Verbatim chunk planning (`chunks.py`)

**Files:**
- Create: `backend/src/octave/context/chunks.py`
- Create: `backend/tests/context/test_chunks.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/context/test_chunks.py`:

```python
"""Verbatim chunk rendering + max_chars safety split (Decisions 4/5)."""

from octave.context.brackets import TurnBracket
from octave.context.chunks import chunk_item_id, plan_chunks, render_bracket
from octave.db.models import Event
from octave.db.types import EventKind


def _event(seq: int, kind: EventKind, payload: dict) -> Event:
    return Event(
        id=f"e{seq}",
        session_id="s_1",
        seq=seq,
        kind=str(kind),
        author_participant_id="p_a1",
        payload=payload,
    )


BRACKET = TurnBracket("a_1", 2, 4)


def _bracket_events() -> list[Event]:
    return [
        _event(2, EventKind.TOOL_CALL, {"tool_name": "web.search", "arguments": '{"q": "x"}'}),
        _event(3, EventKind.TOOL_RESULT, {"content": "Sunny 21C"}),
        _event(4, EventKind.ASSISTANT_MESSAGE, {"content": "It is sunny."}),
    ]


def test_render_verbatim_full_content_labeled() -> None:
    text = render_bracket(_bracket_events(), "Echo")
    assert text == (
        'Tool call: web.search {"q": "x"}\n'
        "Tool result: Sunny 21C\n"
        "Echo: It is sunny."
    )


def test_render_tool_result_without_content_uses_json() -> None:
    events = [_event(2, EventKind.TOOL_RESULT, {"structured": {"a": 1}})]
    assert render_bracket(events, "Echo") == 'Tool result: {"a": 1}'


def test_plan_single_chunk_identity_and_meta() -> None:
    plans = plan_chunks(
        session_id="s_1", bracket=BRACKET, events=_bracket_events(),
        label="Echo", max_chars=6000,
    )
    assert len(plans) == 1
    plan = plans[0]
    assert plan.item_id == "transcript_chunk:s_1:2-4"
    assert plan.name == "Echo · events 2–4"
    assert plan.meta == {
        "session_id": "s_1", "agent_id": "a_1", "seq_start": 2, "seq_end": 4,
        "part": 1, "part_total": 1,
    }


def test_plan_split_at_whitespace_boundary() -> None:
    words = "lorem ipsum dolor sit amet " * 10  # 290 chars
    events = [_event(2, EventKind.ASSISTANT_MESSAGE, {"content": words.strip()})]
    plans = plan_chunks(
        session_id="s_1", bracket=TurnBracket("a_1", 2, 2), events=events,
        label="Echo", max_chars=100,
    )
    assert len(plans) > 1
    assert all(len(p.content) <= 100 for p in plans)
    assert all("  " not in p.content.strip() for p in plans)  # no glued words
    assert plans[0].item_id == "transcript_chunk:s_1:2-2:1"
    assert plans[-1].item_id == f"transcript_chunk:s_1:2-2:{len(plans)}"
    assert plans[0].meta["part_total"] == len(plans)
    assert plans[0].name.endswith(f"(part 1/{len(plans)})")
    # parts rejoin to the original (modulo whitespace normalization at cuts)
    assert " ".join(p.content for p in plans) == f"Echo: {words.strip()}"


def test_split_hard_cut_for_single_long_token() -> None:
    events = [_event(2, EventKind.ASSISTANT_MESSAGE, {"content": "x" * 250})]
    plans = plan_chunks(
        session_id="s_1", bracket=TurnBracket("a_1", 2, 2), events=events,
        label="Echo", max_chars=100,
    )
    assert [len(p.content) for p in plans] == [100, 100, 57]  # "Echo: " prefix


def test_chunk_item_id_no_part_suffix_when_single() -> None:
    assert chunk_item_id("s_1", BRACKET, 1, 1) == "transcript_chunk:s_1:2-4"
    assert chunk_item_id("s_1", BRACKET, 2, 3) == "transcript_chunk:s_1:2-4:2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/context/test_chunks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octave.context.chunks'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/octave/context/chunks.py`:

```python
"""Verbatim transcript-chunk rendering (issue #35).

Distinct from ``octave.agent.summaries.HeadTailDigest``: that renderer is
summarization budget (truncation, omission markers). This one is the
archival record — full content, no truncation, split only by the
``max_chars`` safety valve (design spec Decisions 4/5).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from octave.context.brackets import TurnBracket
from octave.db.models import Event
from octave.db.types import EventKind

__all__ = ["ChunkPlan", "chunk_item_id", "plan_chunks", "render_bracket"]


@dataclass(frozen=True)
class ChunkPlan:
    """One desired ``transcript_chunk`` item: identity, prose, provenance.
    ``meta`` carries everything except ``model_name`` — the archiver fills
    that from the embed result."""

    item_id: str
    name: str
    content: str
    meta: dict[str, Any]


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _event_line(event: Event, label: str) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    if event.kind == EventKind.ASSISTANT_MESSAGE:
        return f"{label}: {payload.get('content', '')}"
    if event.kind == EventKind.TOOL_CALL:
        name = payload.get("tool_name") or payload.get("name") or "tool"
        arguments = payload.get("arguments", "")
        return f"Tool call: {name} {arguments}".rstrip()
    if event.kind == EventKind.TOOL_RESULT:
        content = payload.get("content") or payload.get("result") or _json(
            payload.get("structured", payload)
        )
        return f"Tool result: {content}"
    if event.kind == EventKind.SYSTEM:
        return f"System: {payload.get('content', '')}"
    return _json(payload)


def render_bracket(events: Sequence[Event], label: str) -> str:
    """Full-content labeled lines, seq order, no truncation."""
    ordered = sorted(events, key=lambda e: e.seq)
    return "\n".join(_event_line(e, label) for e in ordered)


def chunk_item_id(
    session_id: str, bracket: TurnBracket, part: int, part_total: int
) -> str:
    """Deterministic id: ``(session_id, seq_range)`` is immutable (events
    never change), so the id doubles as the idempotence key. Split
    brackets carry a ``:part`` suffix."""
    base = f"transcript_chunk:{session_id}:{bracket.seq_start}-{bracket.seq_end}"
    return base if part_total == 1 else f"{base}:{part}"


def _split(text: str, max_chars: int) -> list[str]:
    """Whitespace-boundary split; hard-cut only for a single over-long
    token (guarantees progress: ``cut`` is never 0)."""
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        cut = remaining.rfind(" ", 0, max_chars)
        if cut <= 0:
            cut = max_chars
        parts.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts


def plan_chunks(
    *,
    session_id: str,
    bracket: TurnBracket,
    events: Sequence[Event],
    label: str,
    max_chars: int,
) -> list[ChunkPlan]:
    """One chunk per bracket normally; numbered parts when over max_chars."""
    text = render_bracket(events, label)
    parts = _split(text, max_chars)
    total = len(parts)
    plans: list[ChunkPlan] = []
    for index, part_text in enumerate(parts, start=1):
        name = f"{label} · events {bracket.seq_start}–{bracket.seq_end}"
        if total > 1:
            name += f" (part {index}/{total})"
        plans.append(
            ChunkPlan(
                item_id=chunk_item_id(session_id, bracket, index, total),
                name=name,
                content=part_text,
                meta={
                    "session_id": session_id,
                    "agent_id": bracket.agent_id,
                    "seq_start": bracket.seq_start,
                    "seq_end": bracket.seq_end,
                    "part": index,
                    "part_total": total,
                },
            )
        )
    return plans
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/context/test_chunks.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/context/chunks.py backend/tests/context/test_chunks.py
git commit -m "feat(context): verbatim chunk planning with max_chars safety split"
```

---

### Task 4: `ContextArchiver` — the capture → embed → store orchestrator

**Files:**
- Create: `backend/src/octave/context/archiver.py`
- Modify: `backend/src/octave/context/__init__.py`
- Create: `backend/tests/context/conftest.py`
- Create: `backend/tests/context/fakes.py`
- Create: `backend/tests/context/test_archiver.py`
- Modify: `backend/tests/context/test_package.py` (full export list)

- [ ] **Step 1: Write the test fixtures**

Create `backend/tests/context/conftest.py` (vector-store-enabled env, mirroring the `tests/db/test_vault_store.py` pattern):

```python
"""Vector-store-enabled fixtures for the context plane."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from octave.db.config import DbConfig
from octave.db.models import Base
from octave.db.sqlite_adapter import SqliteVecAdapter

DIM = 4


@pytest_asyncio.fixture
async def env(
    tmp_path: Path,
) -> AsyncIterator[tuple[SqliteVecAdapter, async_sessionmaker]]:
    config = DbConfig(
        adapter="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'context.db'}",
        embedding_dim=DIM,
    )
    adapter = SqliteVecAdapter(config)
    engine: AsyncEngine = adapter.make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await adapter.ensure_vector_store(conn)
    factory = adapter.make_session_factory(engine)
    yield adapter, factory
    await engine.dispose()
```

Create `backend/tests/context/fakes.py`:

```python
"""Deterministic embedding fake for the context plane."""

import hashlib
from collections.abc import AsyncIterator

from octave.inference.adapter import InferenceAdapter
from octave.inference.config import AdapterConfig
from octave.inference.types import (
    CompletionChunk,
    CompletionRequest,
    CompletionResult,
    EmbeddingRequest,
    EmbeddingResult,
    ModelInfo,
)


class FakeEmbedAdapter(InferenceAdapter):
    """Hashes each input to a stable vector; records requests.
    complete() raises — the archiver must never generate prose."""

    def __init__(self, dim: int = 4, model: str = "embed-fake") -> None:
        super().__init__(
            AdapterConfig(adapter="fake", base_url="http://fake.test/v1")
        )
        self._dim = dim
        self._model = model
        self.embed_calls: list[EmbeddingRequest] = []

    def vector(self, text: str) -> list[float]:
        digest = hashlib.md5(text.encode()).digest()
        return [digest[i] / 255.0 for i in range(self._dim)]

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        self.embed_calls.append(request)
        model = request.model or self._model
        return EmbeddingResult(
            embeddings=[self.vector(t) for t in request.inputs], model=model
        )

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        raise AssertionError("ContextArchiver must never call complete()")

    async def stream(
        self, request: CompletionRequest
    ) -> AsyncIterator[CompletionChunk]:
        raise NotImplementedError
        yield CompletionChunk()  # satisfy async-generator typing

    async def list_models(self) -> list[ModelInfo]:
        return []
```

- [ ] **Step 2: Write the failing archiver tests**

Create `backend/tests/context/test_archiver.py`:

```python
"""ContextArchiver: capture → embed → store (design spec, issue #35).

Real SQLite via the env fixture; FakeEmbedAdapter implements embed only.
Transcripts are scripted through EventStore (the router's write path).
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.context import ContextArchiver, SessionNotFound
from octave.context.archiver import _needs_embed
from octave.db.event_store import EventStore
from octave.db.models import Agent, Base, Participant, Session, User
from octave.db.sqlite_adapter import SqliteVecAdapter, vector_table_name
from octave.db.types import ExplicitModelBinding, EventKind, VaultKind
from octave.db.vault_store import VaultStore
from octave.inference.errors import AdapterConnectionError
from tests.context.fakes import FakeEmbedAdapter

_BINDING = ExplicitModelBinding(kind="explicit", adapter="fake", model="embed-fake")

Env = tuple[SqliteVecAdapter, async_sessionmaker]


async def _seed(env: Env) -> None:
    _, factory = env
    async with factory() as s:
        s.add(User(id="u_1", display_name="Alice"))
        s.add(User(id="u_2", display_name="Bob"))
        s.add(Agent(id="a_1", name="Echo", model_binding={"kind": "tag", "tag": "quick"}))
        s.add(Session(id="s_1", created_by_user_id="u_1", status="active"))
        s.add(Participant(id="p_u1", user_id="u_1", label="Alice"))
        s.add(Participant(id="p_a1", agent_id="a_1", label="Echo"))
        await s.commit()


async def _script_transcript(env: Env, *, turns: int = 1) -> None:
    """One turn = tool_call + tool_result + assistant reply; first turn
    preceded by the user trigger."""
    _, factory = env
    async with factory() as s:
        store = EventStore(s)
        await store.append(
            "s_1", EventKind.USER_MESSAGE, author_participant_id="p_u1",
            payload={"content": "weather?"},
        )
        for i in range(turns):
            await store.append(
                "s_1", EventKind.TOOL_CALL, author_participant_id="p_a1",
                payload={"tool_name": "web.search", "arguments": f'{{"q": "q{i}"}}'},
            )
            await store.append(
                "s_1", EventKind.TOOL_RESULT, author_participant_id="p_a1",
                payload={"content": f"Sunny 21C run {i}"},
            )
            await store.append(
                "s_1", EventKind.ASSISTANT_MESSAGE, author_participant_id="p_a1",
                payload={"content": f"It is sunny, run {i}."},
            )
        await s.commit()


async def _archive(
    env: Env, fake: FakeEmbedAdapter, session_id: str = "s_1", **kwargs
):
    adapter, factory = env
    async with factory() as session:
        archiver = ContextArchiver(
            session=session, db_adapter=adapter, embed_binding=_BINDING,
            adapter_for=lambda name: fake, **kwargs,
        )
        report = await archiver.archive(session_id)
        await session.commit()
        return report


async def _chunk_items(env: Env):
    adapter, factory = env
    async with factory() as session:
        return await VaultStore(adapter, session).list_items(
            user_id="u_1", kind=VaultKind.TRANSCRIPT_CHUNK
        )


async def test_happy_path_writes_embedded_chunks(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter()
    report = await _archive(env, fake)
    assert report.turns == 1
    assert report.chunks_written == 1
    assert report.summary_embedded is False
    items = await _chunk_items(env)
    assert [i.id for i in items] == ["transcript_chunk:s_1:2-4"]
    item = items[0]
    assert item.embedding is not None
    assert item.embedding_model == "embed-fake"
    assert item.meta["agent_id"] == "a_1"
    assert item.meta["seq_start"] == 2 and item.meta["seq_end"] == 4
    assert len(fake.embed_calls) == 1


async def test_idempotent_second_pass_is_a_noop(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter()
    await _archive(env, fake)
    report = await _archive(env, fake)
    assert report.chunks_written == 0
    assert report.chunks_skipped == 1
    assert len(fake.embed_calls) == 1  # second pass: no embed call


async def test_multi_turn_single_batched_embed(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env, turns=2)
    fake = FakeEmbedAdapter()
    report = await _archive(env, fake)
    assert report.turns == 2
    assert report.chunks_written == 2
    assert len(fake.embed_calls) == 1
    assert len(fake.embed_calls[0].inputs) == 2


async def test_summary_without_embedding_gets_embedded(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    adapter, factory = env
    async with factory() as session:
        await VaultStore(adapter, session).upsert(
            item_id="session_summary:s_1", user_id="u_1",
            kind=VaultKind.SESSION_SUMMARY, name="Weather check",
            content="Checked the weather: sunny.",
            meta={"session_id": "s_1", "covered_seq": 4, "model_name": "chat-1"},
        )
        await session.commit()
    fake = FakeEmbedAdapter()
    report = await _archive(env, fake)
    assert report.summary_embedded is True
    async with factory() as session:
        summary = await VaultStore(adapter, session).get("session_summary:s_1")
    assert summary is not None
    assert summary.embedding is not None
    assert summary.name == "Weather check"  # read-modify-write preserves prose
    assert summary.meta["covered_seq"] == 4


async def test_summary_absent_skips_tier_one(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter()
    report = await _archive(env, fake)
    assert report.summary_embedded is False


async def test_model_swap_reembeds(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    await _archive(env, FakeEmbedAdapter())
    # new binding model: staleness compares against the resolved model
    adapter, factory = env

    class _SwapBinding(ExplicitModelBinding):
        pass

    async with factory() as session:
        archiver = ContextArchiver(
            session=session, db_adapter=adapter,
            embed_binding=ExplicitModelBinding(
                kind="explicit", adapter="fake", model="embed-new"
            ),
            adapter_for=lambda name: FakeEmbedAdapter(model="embed-new"),
        )
        report = await archiver.archive("s_1")
        await session.commit()
    assert report.chunks_written == 1  # stale by embedding_model mismatch


async def test_max_chars_split_writes_parted_items(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter()
    report = await _archive(env, fake, max_chars=40)
    assert report.chunks_written > 1
    items = await _chunk_items(env)
    ids = sorted(i.id for i in items)
    assert ids[0] == "transcript_chunk:s_1:2-4:1"
    assert all(i.embedding is not None for i in items)


async def test_embed_failure_propagates_with_no_writes(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter()

    async def _boom(request):
        raise AdapterConnectionError("down")

    fake.embed = _boom  # type: ignore[method-assign]
    with pytest.raises(AdapterConnectionError):
        await _archive(env, fake)
    assert await _chunk_items(env) == []


async def test_dimension_mismatch_propagates(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter(dim=8)  # adapter config is DIM=4
    with pytest.raises(BaseException) as excinfo:  # DbDimensionMismatchError
        await _archive(env, fake)
    assert "DbDimensionMismatchError" in type(excinfo.value).__name__


async def test_unknown_session_raises(env: Env) -> None:
    await _seed(env)
    with pytest.raises(SessionNotFound):
        await _archive(env, FakeEmbedAdapter(), session_id="s_missing")


async def test_rollback_leaves_vault_and_vec_clean(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    adapter, factory = env
    async with factory() as session:
        archiver = ContextArchiver(
            session=session, db_adapter=adapter, embed_binding=_BINDING,
            adapter_for=lambda name: FakeEmbedAdapter(),
        )
        await archiver.archive("s_1")
        await session.rollback()
    async with factory() as checker:
        items = await VaultStore(adapter, checker).list_items(
            user_id="u_1", kind=VaultKind.TRANSCRIPT_CHUNK
        )
        assert items == []
        vec_count = (
            await checker.execute(
                text(f"SELECT count(*) FROM {vector_table_name(4)}")
            )
        ).scalar_one()
        assert vec_count == 0


def test_needs_embed_rules() -> None:
    class _Item:
        def __init__(self, embedding, embedding_model):
            self.embedding = embedding
            self.embedding_model = embedding_model

    assert _needs_embed(None, "m") is True
    assert _needs_embed(_Item(None, None), "m") is True
    assert _needs_embed(_Item(b"vec", "other"), "m") is True
    assert _needs_embed(_Item(b"vec", "m"), "m") is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/context/test_archiver.py -v`
Expected: FAIL — `ImportError: cannot import name 'ContextArchiver'`

- [ ] **Step 4: Write the implementation**

Create `backend/src/octave/context/archiver.py`:

```python
"""Capture → embed → store for completed agent runs (issue #35).

Pull-only archival: ``archive(session_id)`` reconstructs turn brackets
from the transcript (:mod:`octave.context.brackets`), writes verbatim
``transcript_chunk`` items, and embeds Tier-1 ``session_summary`` plus
Tier-2 chunks through :class:`octave.db.vault_store.VaultStore`. The
archiver embeds only — it never generates prose
(``octave.agent.summaries.SessionSummarizer`` owns summary text; the
composition root runs ``collect()`` before ``archive()``).

Never commits; callers own transaction boundaries (``octave.db.deps``).
Embed API calls happen before any DB write, so inference latency never
sits inside a transaction. Idempotent: deterministic chunk ids +
"skip iff embedded by the current model" make re-runs no-ops.

``max_chars`` is the chunk safety valve: derive it from the embedding
model's documented INPUT context window (chars ≈ tokens × 3–4,
conservative). It is unrelated to ``embedding_dim`` (vector width).
Chunk boundaries freeze at first archival — changing ``max_chars``
never re-splits already-archived brackets (id exists → skip).
"""

import logging
from collections.abc import Callable, Sequence

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.context.brackets import TurnBracket, reconstruct_turns
from octave.context.chunks import ChunkPlan, plan_chunks
from octave.context.errors import ModelBindingNotResolved, SessionNotFound
from octave.db.adapter import DbAdapter
from octave.db.event_store import EventStore
from octave.db.models import Event, Participant, Session, VaultItem
from octave.db.types import ExplicitModelBinding, ModelBinding, VaultKind
from octave.db.vault_store import VaultStore
from octave.inference.adapter import InferenceAdapter
from octave.inference.types import EmbeddingRequest

__all__ = ["ArchiveReport", "ContextArchiver", "DEFAULT_MAX_CHARS"]

logger = logging.getLogger(__name__)

#: Conservative default for common 8k-token embedding endpoints
#: (~2k tokens × ~3 chars/token). The composition root overrides per
#: deployed model; no auto-detection ships (design spec Decision 5).
DEFAULT_MAX_CHARS = 6000


@dataclass(frozen=True)
class ArchiveReport:
    """What one archive() pass did. For logging/tests; nothing persists it."""

    session_id: str
    turns: int
    summary_embedded: bool
    chunks_written: int
    chunks_skipped: int


def _resolve_binding(
    binding: ModelBinding,
    *,
    tag_lookup: Callable[[str], str | None] | None = None,
) -> tuple[str | None, str]:
    """(adapter_name_or_None, model). Duplicated from
    ``octave.agent.instances.resolve_model``: the plane ban forbids the
    import; a third consumer triggers extraction to a neutral home
    (design spec open question)."""
    if isinstance(binding, ExplicitModelBinding):
        return binding.adapter, binding.model
    if tag_lookup is None:
        raise ModelBindingNotResolved(
            f"tag binding {binding.tag!r} requires a tag_lookup"
        )
    model = tag_lookup(binding.tag)
    if model is None:
        raise ModelBindingNotResolved(f"model tag {binding.tag!r} resolves to nothing")
    return None, model


def _needs_embed(item: VaultItem | None, model: str) -> bool:
    """Absent, never embedded (#28 regeneration NULLs the cache), or
    embedded by a different model (config swap) → (re-)embed. Comparison
    is against the resolved binding model; ``embedding_model`` stores what
    the server reported (``result.model``, matching #28)."""
    if item is None:
        return True
    return item.embedding is None or item.embedding_model != model


class ContextArchiver:
    """Pull-only archival of completed agent runs. Never commits."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        db_adapter: DbAdapter,
        embed_binding: ModelBinding,
        adapter_for: Callable[[str | None], InferenceAdapter],
        tag_lookup: Callable[[str], str | None] | None = None,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be >= 1")
        self._session = session
        self._vault = VaultStore(db_adapter, session)
        self._events = EventStore(session)
        self._embed_binding = embed_binding
        self._adapter_for = adapter_for
        self._tag_lookup = tag_lookup
        self._max_chars = max_chars

    async def archive(self, session_id: str) -> ArchiveReport:
        session_row = await self._session.get(Session, session_id)
        if session_row is None:
            raise SessionNotFound(session_id)
        events = await self._events.read(session_id)
        brackets = reconstruct_turns(events, await self._agent_map(events))
        labels = await self._labels(brackets)

        plans: list[ChunkPlan] = []
        for bracket in brackets:
            bracket_events = [
                e for e in events if bracket.seq_start <= e.seq <= bracket.seq_end
            ]
            plans.extend(
                plan_chunks(
                    session_id=session_id,
                    bracket=bracket,
                    events=bracket_events,
                    label=labels.get(bracket.agent_id, bracket.agent_id),
                    max_chars=self._max_chars,
                )
            )
        summary = await self._vault.get(f"session_summary:{session_id}")

        adapter_name, model = _resolve_binding(
            self._embed_binding, tag_lookup=self._tag_lookup
        )
        existing: dict[str, VaultItem | None] = {}
        for plan in plans:
            existing[plan.item_id] = await self._vault.get(plan.item_id)
        pending = [p for p in plans if _needs_embed(existing[p.item_id], model)]
        summary_pending = summary is not None and _needs_embed(summary, model)
        if not pending and not summary_pending:
            return ArchiveReport(
                session_id=session_id,
                turns=len(brackets),
                summary_embedded=False,
                chunks_written=0,
                chunks_skipped=len(plans),
            )

        texts = [p.content for p in pending]
        if summary is not None and summary_pending:
            texts.append(summary.content)
        adapter = self._adapter_for(adapter_name)
        result = await adapter.embed(EmbeddingRequest(model=model, inputs=texts))
        if len(result.embeddings) != len(texts):
            raise ValueError(
                f"embed returned {len(result.embeddings)} vectors for "
                f"{len(texts)} inputs"
            )

        for plan, vector in zip(pending, result.embeddings):
            await self._vault.upsert(
                item_id=plan.item_id,
                user_id=session_row.created_by_user_id,
                kind=VaultKind.TRANSCRIPT_CHUNK,
                name=plan.name,
                content=plan.content,
                meta={**plan.meta, "model_name": result.model},
                embedding=vector,
                embedding_model=result.model,
            )
        if summary is not None and summary_pending:
            # read-modify-write: preserve #28's name/content/meta
            # (2026-09-29 ADR — one summary, two consumers).
            await self._vault.upsert(
                item_id=summary.id,
                user_id=summary.user_id,
                kind=VaultKind(summary.kind),
                name=summary.name,
                content=summary.content,
                meta=summary.meta,
                embedding=result.embeddings[-1],
                embedding_model=result.model,
            )

        logger.debug(
            "context archived | session=%s turns=%s chunks=%s skipped=%s "
            "summary=%s",
            session_id,
            len(brackets),
            len(pending),
            len(plans) - len(pending),
            summary_pending,
        )
        return ArchiveReport(
            session_id=session_id,
            turns=len(brackets),
            summary_embedded=bool(summary_pending),
            chunks_written=len(pending),
            chunks_skipped=len(plans) - len(pending),
        )

    async def _agent_map(self, events: Sequence[Event]) -> dict[str, str]:
        """participant_id -> agent_id for agent participants among authors."""
        participant_ids = {
            e.author_participant_id for e in events if e.author_participant_id
        }
        if not participant_ids:
            return {}
        rows = await self._session.execute(
            select(Participant.id, Participant.agent_id).where(
                Participant.id.in_(participant_ids),
                Participant.agent_id.is_not(None),
            )
        )
        return {pid: aid for pid, aid in rows.all()}

    async def _labels(self, brackets: Sequence[TurnBracket]) -> dict[str, str]:
        """agent_id -> display label (same Participant.label #28 renders)."""
        agent_ids = {b.agent_id for b in brackets}
        if not agent_ids:
            return {}
        rows = await self._session.execute(
            select(Participant.agent_id, Participant.label).where(
                Participant.agent_id.in_(agent_ids)
            )
        )
        return {aid: label for aid, label in rows.all() if aid is not None}
```

Update `backend/src/octave/context/__init__.py` (full export surface):

```python
"""Context Manager service layer (issue #35).

Archives completed agent runs into the vault for future linked runs to
query. Imports ``octave.db`` + ``octave.inference`` only — never
``octave.agent`` (design spec 2026-09-30, Decision 9).
"""

from octave.context.archiver import ArchiveReport, ContextArchiver
from octave.context.brackets import TurnBracket, reconstruct_turns
from octave.context.errors import ModelBindingNotResolved, SessionNotFound

__all__ = [
    "ArchiveReport",
    "ContextArchiver",
    "ModelBindingNotResolved",
    "SessionNotFound",
    "TurnBracket",
    "reconstruct_turns",
]
```

- [ ] **Step 5: Update the package guard test to the full surface**

Replace `backend/tests/context/test_package.py` entirely with:

```python
"""Public API surface and plane-boundary posture of the context package."""

import ast
from pathlib import Path

import octave.context as context_pkg

SDK_MODULES = {"openai", "mcp"}
BANNED_OCTAVE_MODULES = {"agent"}


def _imported_top_level_octave_modules() -> set[str]:
    names: set[str] = set()
    for path in Path(context_pkg.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")
                if top[0] == "octave" and len(top) > 1:
                    names.add(top[1])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")
                    if top[0] == "octave" and len(top) > 1:
                        names.add(top[1])
    return names


def test_no_sdk_imports() -> None:
    """octave.context composes Octave façades only, never SDKs."""
    assert not _imported_top_level_octave_modules() & SDK_MODULES


def test_no_agent_plane_imports() -> None:
    """The CM plane must not import octave.agent (design spec Decision 9)."""
    assert not _imported_top_level_octave_modules() & BANNED_OCTAVE_MODULES


def test_public_names_are_exported() -> None:
    for name in (
        "ArchiveReport",
        "ContextArchiver",
        "ModelBindingNotResolved",
        "SessionNotFound",
        "TurnBracket",
        "reconstruct_turns",
    ):
        assert hasattr(context_pkg, name), name
```

- [ ] **Step 6: Run the full context suite to verify it passes**

Run: `uv run pytest tests/context -v`
Expected: all PASSED (package 3, brackets 7, chunks 6, archiver 12)

- [ ] **Step 7: Commit**

```bash
git add backend/src/octave/context backend/tests/context
git commit -m "feat(context): pull-only ContextArchiver with batched embedding and idempotent re-runs"
```

---

### Task 5: Search end-to-end + router equivalence

**Files:**
- Modify: `backend/tests/context/test_brackets.py` (append router equivalence test)
- Create: `backend/tests/context/test_search.py`

- [ ] **Step 1: Append the router equivalence test**

Append to `backend/tests/context/test_brackets.py`:

```python
async def test_matches_router_turn_records(session_factory) -> None:
    """The reconstruction rule agrees with the shipped router's claim
    points on a committed transcript (design spec Decision 3's pin)."""
    from sqlalchemy import select

    from octave.agent import AgentInstanceManager, AgentRegistry
    from octave.agent.decider import Decision, DecisionState
    from octave.agent.router import MessageRouter
    from octave.db.models import Agent, Participant, Session, SessionParticipant, User

    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(Agent(id="a_1", name="Echo", model_binding={"kind": "tag", "tag": "quick"}))
        session.add(Agent(id="a_2", name="Second", model_binding={"kind": "tag", "tag": "quick"}))
        session.add(Session(id="s_1", created_by_user_id="u_1", status="active"))
        await session.commit()

    async def _spawn(agent_id: str) -> None:
        async with session_factory() as session:
            await AgentInstanceManager(session).spawn(agent_id=agent_id, session_id="s_1")
            await session.commit()

    async def _participant_of(agent_id: str) -> str:
        async with session_factory() as session:
            row = await session.execute(select(Participant.id).where(Participant.agent_id == agent_id))
            return row.scalar_one()

    async with session_factory() as session:
        session.add(Participant(id="p_u1", user_id="u_1", label="Alice"))
        await session.flush()
        session.add(SessionParticipant(session_id="s_1", participant_id="p_u1"))
        await session.commit()

    await _spawn("a_1")
    await _spawn("a_2")
    p_a1 = await _participant_of("a_1")
    p_a2 = await _participant_of("a_2")

    class ScriptedDecider:
        def __init__(self, choices: list[str]) -> None:
            self._choices = list(choices)

        async def decide(self, state: DecisionState) -> str:
            return self._choices.pop(0)

    class RecordingRunner:
        def __init__(self, replies: list[str]) -> None:
            self._replies = list(replies)

        async def run_turn(self, *, instance, messages) -> str:  # type: ignore[no-untyped-def]
            return self._replies.pop(0)

    async with session_factory() as session:
        router = MessageRouter(
            session=session,
            manager=AgentInstanceManager(session),
            registry=AgentRegistry(session),
            decider=ScriptedDecider([p_a1, p_a2, Decision.AWAIT_USER]),
            turn_runner=RecordingRunner(["first", "second"]),
        )
        outcome = await router.deliver("s_1", author_participant_id="p_u1", content="go")
        await session.commit()

    async with session_factory() as session:
        events = await EventStore(session).read("s_1")
        rows = await session.execute(
            select(Participant.id, Participant.agent_id).where(Participant.agent_id.is_not(None))
        )
        agent_by_participant = {pid: aid for pid, aid in rows.all()}

    brackets = reconstruct_turns(events, agent_by_participant)
    assert [(b.agent_id, b.seq_start, b.seq_end) for b in brackets] == [
        (t.agent_id, t.seq_start, t.seq_end) for t in outcome.turns
    ]
```

Also add to the imports at the top of `test_brackets.py`: `from octave.db.event_store import EventStore`.

- [ ] **Step 2: Run to verify equivalence passes**

Run: `uv run pytest tests/context/test_brackets.py -v`
Expected: 8 PASSED (the equivalence test passes because the rule matches the shipped router; if it fails, the rule — not the test — is wrong: re-read the bracket docstring and fix `brackets.py`, then re-run)

- [ ] **Step 3: Write the search end-to-end test**

Create `backend/tests/context/test_search.py`:

```python
"""The archived corpus is findable through shipped VaultStore.search
(design spec §6: this item discharges 'future linked runs can query it').
"""

from octave.context import ContextArchiver
from octave.db.types import ExplicitModelBinding, VaultKind
from octave.db.vault_store import VaultStore
from tests.context.fakes import FakeEmbedAdapter
from tests.context.test_archiver import Env, _archive, _script_transcript, _seed

_BINDING = ExplicitModelBinding(kind="explicit", adapter="fake", model="embed-fake")


async def test_archived_chunks_are_searchable(env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter()
    await _archive(env, fake)
    adapter, factory = env
    async with factory() as session:
        store = VaultStore(adapter, session)
        # A query identical to the chunk content → identical vector → d=0.
        chunk = await store.get("transcript_chunk:s_1:2-4")
        assert chunk is not None
        hits = await store.search(
            user_id="u_1", embedding=fake.vector(chunk.content),
            kind=VaultKind.TRANSCRIPT_CHUNK,
        )
        assert [h.item.id for h in hits] == ["transcript_chunk:s_1:2-4"]
        # Session-scoped Tier-2 search (the aux-column filter from #32).
        scoped = await store.search(
            user_id="u_1", embedding=fake.vector(chunk.content),
            kind=VaultKind.TRANSCRIPT_CHUNK, session_id="s_1",
        )
        assert len(scoped) == 1
        wrong = await store.search(
            user_id="u_1", embedding=fake.vector(chunk.content),
            kind=VaultKind.TRANSCRIPT_CHUNK, session_id="s_other",
        )
        assert wrong == []
        # Other users never see them (search is always user-scoped).
        foreign = await store.search(
            user_id="u_2", embedding=fake.vector(chunk.content),
            kind=VaultKind.TRANSCRIPT_CHUNK,
        )
        assert foreign == []


async def test_embedded_summary_is_tier_one_findable(env) -> None:
    await _seed(env)
    await _script_transcript(env)
    adapter, factory = env
    async with factory() as session:
        await VaultStore(adapter, session).upsert(
            item_id="session_summary:s_1", user_id="u_1",
            kind=VaultKind.SESSION_SUMMARY, name="Weather check",
            content="Checked the weather: sunny.",
            meta={"session_id": "s_1", "covered_seq": 4, "model_name": "chat-1"},
        )
        await session.commit()
    fake = FakeEmbedAdapter()
    await _archive(env, fake)
    async with factory() as session:
        store = VaultStore(adapter, session)
        summary = await store.get("session_summary:s_1")
        assert summary is not None
        hits = await store.search(
            user_id="u_1", embedding=fake.vector(summary.content),
            kind=VaultKind.SESSION_SUMMARY,
        )
        assert [h.item.id for h in hits] == ["session_summary:s_1"]
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/context/test_search.py tests/context/test_brackets.py -v`
Expected: all PASSED

- [ ] **Step 5: Run the full gates**

Run from `backend/`: `uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all green. Fix any ruff/mypy findings (import order, unused imports, type annotations) before committing.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/context
git commit -m "test(context): router equivalence and search end-to-end over archived corpus"
```

---

### Task 6: Documentation — ADR + TODO

**Files:**
- Modify: `.agents/memory/decisions.md` (append ADR)
- Modify: `docs/TODO.md` (mark CM #5 done)

- [ ] **Step 1: Append the ADR**

Append to the end of `.agents/memory/decisions.md`:

```markdown
### 2026-09-30 — Pull-Only Context Archival via Reconstructed Turn Brackets

**Context:** Issue #35 (CM #5): capture completed agent runs, embed into
the vector DB for future linked runs to query. #28 shipped session
summaries with NULL embeddings (embed deferred here); the 2026-09-20 ADR
defined `transcript_chunk` but no writer existed. `TurnRecord` is
ephemeral — returned in `RouteOutcome.turns`, never persisted.

**Options Considered:** 1) push — callers pass `RouteOutcome.turns` to
the archiver (exact claim points, but every consumer must hold and
forward them); 2) pull — `archive(session_id)` reconstructs brackets from
the transcript (bracket = `(max(prev_agent_reply_seq,
last_non_agent_event_seq), reply_seq]`; failed turns close nothing, so
orphan events produce no bracket — same outcome as push); 3) persist
`TurnRecord`s in a new table (schema change to store what the transcript
already encodes).

**Decision:** Option 2, and both tiers ship. New package
`octave/context/` (CM's first service module): pure `brackets.py` +
`chunks.py`, one `ContextArchiver.archive(session_id)` orchestrator.
Chunk-per-bracket, verbatim (distinct from digest rendering),
deterministic id `transcript_chunk:<session_id>:<seq_start>-<seq_end>`
(+`:part` for the `max_chars` safety split at whitespace). Embedding via
a dedicated `ModelBinding` + `adapter_for` seam; re-embed iff
`embedding IS NULL` or `embedding_model != resolved model`. The archiver
embeds only — never generates prose; summary-absent skips Tier-1.
`octave.context` never imports `octave.agent`: binding resolution is
duplicated (~15 lines) rather than imported; CM-local
`SessionNotFound`/`ModelBindingNotResolved` mirror agent error names with
independent types. `max_chars` derives from the embedding model's INPUT
context window (chars ≈ tokens × 3–4, conservative default 6000) — not
`embedding_dim`; boundaries freeze at first archival.

**Rationale:** Reconstruction provably equals router claim points on
committed transcripts (equivalence test pinned against the shipped
router). Deterministic ids + immutable events make idempotence fall out
— no watermark table, no invalidation machinery. One entry point serves
post-deliver(), backfill, and future scheduling (AM #6) identically.

**Consequences:** The vault's Tier-2 corpus gains its first writer; the
"future linked runs can query it" promise is discharged through the
shipped filtered `VaultStore.search` (proven end-to-end), not a new
query surface (CM #11). `max_chars` changes never re-split archived
brackets. CM #10 (conversation indexing) overlap recorded as an open
question. Resolver duplication is debt: extract on a third consumer.
```

- [ ] **Step 2: Mark CM #5 done**

In `docs/TODO.md`, replace the line:

```markdown
- [ ] 5. Implement agent context lifecycle (receive full context from completed agent runs via Agent Manager, embed into vector DB for future linked agent runs to query) [Depends on: Agent Manager #1–4]
```

with:

```markdown
- [x] 5. Implement agent context lifecycle (receive full context from completed agent runs via Agent Manager, embed into vector DB for future linked agent runs to query) — PR #114 (pull-only `ContextArchiver` over reconstructed turn brackets; verbatim `transcript_chunk` writer; Tier-1 `session_summary` embedding via read-modify-write; new `octave.context` plane)
```

- [ ] **Step 3: Commit**

```bash
git add .agents/memory/decisions.md docs/TODO.md
git commit -m "docs(context): ADR for pull-only archival; mark CM #5 done (PR #114)"
```

---

## Self-Review (completed at plan writing)

1. **Spec coverage:** Decision 1 (both tiers) → Tasks 3/4; Decision 2 (pull-only) → Task 4; Decision 3 (reconstruction + pin) → Tasks 2 & 5; Decisions 4/5 (chunking, max_chars) → Task 3; Decision 6 (binding seam) → Task 4; Decision 7 (re-embed rule) → Task 4 (`_needs_embed` tests); Decision 8 (embed-only) → `FakeEmbedAdapter.complete` raises, Task 4; Decision 9 (package + ban) → Task 1 guards; Decision 10 (search proof) → Task 5. Errors section → Task 1 + Task 4 error tests. Testing table → all four test files covered.
2. **Placeholder scan:** none — every code step shows complete code; every command shows expected result.
3. **Type consistency:** `TurnBracket(agent_id, seq_start, seq_end)`, `reconstruct_turns(events, agent_by_participant)`, `ChunkPlan(item_id, name, content, meta)`, `plan_chunks(*, session_id, bracket, events, label, max_chars)`, `ContextArchiver(session=, db_adapter=, embed_binding=, adapter_for=, tag_lookup=, max_chars=)`, `ArchiveReport(session_id, turns, summary_embedded, chunks_written, chunks_skipped)` — used identically across Tasks 1–5.
