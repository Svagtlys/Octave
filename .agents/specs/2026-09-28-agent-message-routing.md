# Agent Message Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the session-level `MessageRouter` — turn-taking driver with an LLM `TurnDecider`, a `TurnRunner` port, and `EventStore`, the first transcript write path with gap-free `seq` (issue #27).

**Architecture:** `octave.db.event_store.EventStore` owns transcript appends (gap-free per-session `seq`, per-kind payload validation, VaultStore transaction convention). `octave.agent.router.MessageRouter.deliver()` appends the user message, then loops: decider picks an idle instance → `begin_turn` (the #25 mutex) → injected `TurnRunner` port → append `assistant_message` → `end_turn` → repeat until `AWAIT_USER` / `HOP_LIMIT` / `ERROR`. `octave.agent.decider.LlmTurnDecider` is the default strategy (1:1 fast path = zero LLM calls). Library-level only — no routes; Integration #1 wires the real runner.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async ORM (`select`, `func.max`, `begin_nested` savepoints), Pydantic payload models + `TypeAdapter`-free direct model validation, `InferenceAdapter` over `ScriptedAdapter` fakes, pytest + pytest-asyncio against real SQLite (shared `session_factory` fixture in `backend/tests/conftest.py`).

**Spec:** [`.agents/specs/2026-09-28-agent-message-routing-design.md`](2026-09-28-agent-message-routing-design.md) · Branch: `feature/agent-message-routing` · PR: #107

**Commands run from `backend/`.** Test style mirrors `tests/agent/test_registry.py`: real SQLite via `session_factory`, explicit commits, scripted fakes (no mock libraries).

---

## File Structure

| File | Responsibility |
|---|---|
| Create `backend/src/octave/db/event_store.py` | `EventStore` — transcript append (gap-free seq + savepoint retry) and read. The only event write path. |
| Create `backend/src/octave/agent/decider.py` | `Candidate`, `DecisionState`, `Decision`, `TurnDecider` protocol, `LlmTurnDecider`. |
| Create `backend/src/octave/agent/router.py` | `TurnRunner` protocol, `StopReason`, `TurnRecord`, `RouteOutcome`, `MessageRouter`, transcript→Message mapping. |
| Modify `backend/src/octave/agent/errors.py` | `RoutingError`, `NotAMemberError`, `DeciderChoiceError`. |
| Modify `backend/src/octave/db/__init__.py` | Export `EventStore`. |
| Modify `backend/src/octave/agent/__init__.py` | Export the router/decider public surface. |
| Create `backend/tests/db/test_event_store.py` | EventStore tests. |
| Create `backend/tests/agent/test_decider.py` | LlmTurnDecider tests. |
| Create `backend/tests/agent/test_router.py` | MessageRouter driver tests. |
| Modify `backend/tests/agent/test_package.py` | Assert the new public names. |
| Modify `docs/TODO.md` | Mark Agent Manager #3 done. |

---

### Task 0: Commit the design doc

**Files:**
- Commit: `.agents/specs/2026-09-28-agent-message-routing-design.md` (already written)

- [ ] **Step 1: Commit**

```bash
git add .agents/specs/2026-09-28-agent-message-routing-design.md
git commit -m "docs(specs): add agent message routing design (#27)"
```

---

### Task 1: `EventStore` — transcript write path

**Files:**
- Create: `backend/src/octave/db/event_store.py`
- Create: `backend/tests/db/test_event_store.py`
- Modify: `backend/src/octave/db/__init__.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/db/test_event_store.py`:

```python
"""EventStore: gap-free transcript appends (design spec 2026-09-28).

Real SQLite via session_factory; the uq_events_session_seq constraint is
the backstop for the seq race, exercised via a scripted stale-read subclass.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import octave.db as db_pkg
from octave.db.event_store import EventStore
from octave.db.models import Participant, Session, User
from octave.db.types import EventKind


async def _seed(session_factory: async_sessionmaker[AsyncSession]) -> str:
    """User u_1, sessions s_1/s_2, and a user participant (author FK)."""
    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(Session(id="s_1", created_by_user_id="u_1", status="active"))
        session.add(Session(id="s_2", created_by_user_id="u_1", status="active"))
        participant = Participant(id="p_u1", user_id="u_1", label="Alice")
        session.add(participant)
        await session.commit()
    return "p_u1"


async def _append(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: str,
    author_id: str,
    content: str,
) -> int:
    async with session_factory() as session:
        event = await EventStore(session).append(
            session_id,
            EventKind.USER_MESSAGE,
            author_participant_id=author_id,
            payload={"content": content},
        )
        await session.commit()
        return event.seq


async def test_seq_starts_at_one_and_increments(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    author = await _seed(session_factory)
    assert await _append(session_factory, "s_1", author, "one") == 1
    assert await _append(session_factory, "s_1", author, "two") == 2


async def test_seq_scoped_per_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    author = await _seed(session_factory)
    assert await _append(session_factory, "s_1", author, "a") == 1
    assert await _append(session_factory, "s_2", author, "b") == 1
    assert await _append(session_factory, "s_1", author, "c") == 2


async def test_read_orders_and_windows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    author = await _seed(session_factory)
    for text in ("one", "two", "three"):
        await _append(session_factory, "s_1", author, text)
    async with session_factory() as session:
        store = EventStore(session)
        everything = await store.read("s_1")
        tail = await store.read("s_1", after_seq=2)
    assert [e.seq for e in everything] == [1, 2, 3]
    assert [e.payload["content"] for e in everything] == ["one", "two", "three"]
    assert [e.seq for e in tail] == [3]


async def test_validates_user_message_payload(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    author = await _seed(session_factory)
    async with session_factory() as session:
        with pytest.raises(ValidationError):
            await EventStore(session).append(
                "s_1", EventKind.USER_MESSAGE, author_participant_id=author, payload={}
            )


async def test_assistant_payload_defaults_model_name_null(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    author = await _seed(session_factory)
    async with session_factory() as session:
        event = await EventStore(session).append(
            "s_1",
            EventKind.ASSISTANT_MESSAGE,
            author_participant_id=author,
            payload={"content": "hi"},
        )
        assert event.payload == {"content": "hi", "model_name": None}


async def test_tool_and_system_kinds_pass_through(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed(session_factory)
        event = await EventStore(session).append(
            "s_1", EventKind.TOOL_CALL, payload={"server_id": "s", "tool": "t"}
        )
        assert event.payload == {"server_id": "s", "tool": "t"}


async def test_never_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    async with session_factory() as session:
        await EventStore(session).append(
            "s_1", EventKind.SYSTEM, payload={"content": "uncommitted"}
        )
    async with session_factory() as other:
        assert await EventStore(other).read("s_1") == []  # not committed yet
    async with session_factory() as session:
        await EventStore(session).append(
            "s_1", EventKind.SYSTEM, payload={"content": "committed"}
        )
        await session.commit()
    async with session_factory() as other:
        rows = await EventStore(other).read("s_1")
    assert [e.payload["content"] for e in rows] == ["committed"]


class _StaleReadStore(EventStore):
    """Scripts stale seq reads to exercise the IntegrityError retry path:
    real cross-connection races are invisible to a stale SQLite snapshot,
    so the retry loop is tested at its seam."""

    def __init__(self, session: AsyncSession, stale: list[int]) -> None:
        super().__init__(session)
        self._stale = stale

    async def _next_seq(self, session_id: str) -> int:
        if self._stale:
            return self._stale.pop(0)
        return await super()._next_seq(session_id)


async def test_retry_recovers_from_seq_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    async with session_factory() as winner:
        await EventStore(winner).append("s_1", EventKind.SYSTEM, payload={"c": 1})
        await winner.commit()  # committed seq=1
    async with session_factory() as loser:
        store = _StaleReadStore(loser, stale=[1])  # stale read -> collides
        event = await store.append("s_1", EventKind.SYSTEM, payload={"c": 2})
        assert event.seq == 2  # retry recomputed from fresh max
        await loser.commit()


async def test_exported_from_package() -> None:
    assert hasattr(db_pkg, "EventStore")
    assert "EventStore" in db_pkg.__all__
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/db/test_event_store.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'octave.db.event_store'`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/src/octave/db/event_store.py`:

```python
"""Transcript write path: append/read events with gap-free seq (issue #27).

``events.seq`` is documented as per-session monotonic (gap-free); clock
resolution cannot guarantee this, so seq assignment has exactly one owner.
Follows the VaultStore convention — constructed with the caller's
AsyncSession, never commits; callers own transaction boundaries
(``octave.db.deps``). The uq_events_session_seq constraint is the backstop
for the seq race: the insert runs in a SAVEPOINT so a collision is
recoverable without poisoning the caller's transaction.

Payload validation: ``user_message``/``assistant_message`` validate against
their ``octave.db.types`` models; ``tool_call``/``tool_result``/``system``
pass through as dicts (validation assigned to consumers, per the types
module docstring).
"""

import uuid
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from octave.db.models import Event
from octave.db.types import (
    AssistantMessagePayload,
    EventKind,
    UserMessagePayload,
)

__all__ = ["EventStore"]

_PAYLOAD_MODELS: dict[EventKind, type[BaseModel]] = {
    EventKind.USER_MESSAGE: UserMessagePayload,
    EventKind.ASSISTANT_MESSAGE: AssistantMessagePayload,
}

_RETRY_LIMIT = 2


class EventStore:
    """The canonical event append/read surface. Never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        session_id: str,
        kind: EventKind,
        *,
        author_participant_id: str | None = None,
        target_participant_id: str | None = None,
        payload: dict[str, Any],
    ) -> Event:
        """Append one event, assigning the next gap-free ``seq``.

        Malformed payloads for validated kinds raise ``ValidationError``
        (a write-path bug — fail loud). On a ``seq`` collision the insert
        retries with a freshly computed seq up to ``_RETRY_LIMIT`` times,
        then re-raises. Cross-connection effectiveness is engine-dependent
        (SQLite snapshots stale reads within a transaction); the constraint
        guarantees no duplicate ever lands either way.
        """
        validator = _PAYLOAD_MODELS.get(kind)
        if validator is not None:
            payload = validator.model_validate(payload).model_dump()
        last_error: IntegrityError | None = None
        for _ in range(_RETRY_LIMIT + 1):
            seq = await self._next_seq(session_id)
            event = Event(
                id=uuid.uuid4().hex,
                session_id=session_id,
                seq=seq,
                kind=str(kind),
                author_participant_id=author_participant_id,
                target_participant_id=target_participant_id,
                payload=payload,
            )
            try:
                async with self._session.begin_nested():
                    self._session.add(event)
                    await self._session.flush()
            except IntegrityError as exc:
                last_error = exc
                continue
            return event
        assert last_error is not None
        raise last_error

    async def read(self, session_id: str, *, after_seq: int = 0) -> list[Event]:
        """Transcript window ``seq > after_seq``, ordered."""
        rows = await self._session.execute(
            select(Event)
            .where(Event.session_id == session_id, Event.seq > after_seq)
            .order_by(Event.seq)
        )
        return list(rows.scalars().all())

    async def _next_seq(self, session_id: str) -> int:
        """Next seq for this session (1-based, gap-free). Autoflush makes
        same-session prior appends visible; overridable seam for retry
        testing."""
        current = await self._session.execute(
            select(func.coalesce(func.max(Event.seq), 0)).where(
                Event.session_id == session_id
            )
        )
        return int(current.scalar_one()) + 1
```

- [ ] **Step 4: Export from `octave.db`**

Modify `backend/src/octave/db/__init__.py` — add the import after the `DbError` import line and the export in `__all__` (alphabetical placement):

```python
from octave.db.errors import DbError
from octave.db.event_store import EventStore
```

```python
__all__ = [
    "Base",
    "DbAdapter",
    "DbAdapterRegistry",
    "DbConfig",
    "DbError",
    "DatabaseSettings",
    "EventKind",
    "EventStore",
    ...
]
```

(Keep the rest of `__all__` exactly as it is; insert `"EventStore"` after `"EventKind"`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/db/test_event_store.py -v`
Expected: all PASS (9 tests).

- [ ] **Step 6: Lint + type check, then commit**

Run: `cd backend && uv run ruff check src tests && uv run mypy src`
Expected: no errors.

```bash
git add backend/src/octave/db/event_store.py backend/src/octave/db/__init__.py backend/tests/db/test_event_store.py
git commit -m "feat(db): add EventStore transcript write path (#27)"
```

---

### Task 2: Routing errors

**Files:**
- Modify: `backend/src/octave/agent/errors.py`
- Modify: `backend/src/octave/agent/__init__.py`
- Modify: `backend/tests/agent/test_package.py`

- [ ] **Step 1: Write the failing test**

Modify `backend/tests/agent/test_package.py` — extend the `test_public_names_are_exported` tuple with the new names (Task 5 adds the router/decider names; this task adds only the errors):

```python
def test_public_names_are_exported() -> None:
    for name in (
        "AgentError",
        "AgentInstanceManager",
        "AgentNotFoundError",
        "AgentPausedError",
        "AgentRegistry",
        "DeciderChoiceError",
        "InstanceExistsError",
        "InstanceNotFoundError",
        "InvalidTransitionError",
        "McpToolExecutor",
        "ModelBindingError",
        "NotAMemberError",
        "ResolvedModel",
        "RoutingError",
        "RunningAgent",
        "SessionNotFoundError",
        "TerminalSessionError",
        "ToolError",
        "ToolExecutor",
        "ToolLoop",
        "ToolLoopLimitError",
        "ToolOutcome",
        "ToolTurn",
        "TurnInProgressError",
        "resolve_model",
    ):
        assert hasattr(agent_pkg, name), name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/agent/test_package.py -v`
Expected: FAIL — `AssertionError: DeciderChoiceError` (first new name).

- [ ] **Step 3: Implement**

Modify `backend/src/octave/agent/errors.py` — append after `ModelBindingError` and before `ToolLoopLimitError`:

```python
class RoutingError(AgentError):
    """Base for router failures (design spec 2026-09-28)."""


class NotAMemberError(RoutingError):
    """Author is not a current member of the session (left_at set or
    never invited)."""


class DeciderChoiceError(RoutingError):
    """Decider output malformed or outside the roster. Raised by deciders;
    the driver owns retry + await-user fallback."""
```

Add the three names to `errors.py` `__all__` (alphabetical, matching existing style):

```python
__all__ = [
    "AgentError",
    "AgentNotFoundError",
    "AgentPausedError",
    "DeciderChoiceError",
    "InstanceExistsError",
    "InstanceNotFoundError",
    "InvalidTransitionError",
    "ModelBindingError",
    "NotAMemberError",
    "RoutingError",
    "SessionNotFoundError",
    "TerminalSessionError",
    "ToolLoopLimitError",
    "TurnInProgressError",
]
```

Modify `backend/src/octave/agent/__init__.py` — extend the errors import and `__all__`:

```python
from octave.agent.errors import (
    AgentError,
    AgentNotFoundError,
    AgentPausedError,
    DeciderChoiceError,
    InstanceExistsError,
    InstanceNotFoundError,
    InvalidTransitionError,
    ModelBindingError,
    NotAMemberError,
    RoutingError,
    SessionNotFoundError,
    TerminalSessionError,
    ToolLoopLimitError,
    TurnInProgressError,
)
```

In `__all__`, insert `"DeciderChoiceError"` after `"AgentRegistry"`, and `"NotAMemberError"` + `"RoutingError"` after `"ModelBindingError"` (keep alphabetical).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/agent/test_package.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/agent/errors.py backend/src/octave/agent/__init__.py backend/tests/agent/test_package.py
git commit -m "feat(agent): add routing error hierarchy (#27)"
```

---

### Task 3: `TurnDecider` + `LlmTurnDecider`

**Files:**
- Create: `backend/src/octave/agent/decider.py`
- Create: `backend/tests/agent/test_decider.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/agent/test_decider.py`:

```python
"""LlmTurnDecider: 1:1 fast path + roster-validated JSON choice (spec #27)."""

import pytest
from octave.agent.decider import Candidate, Decision, DecisionState, LlmTurnDecider
from octave.agent.errors import DeciderChoiceError
from octave.inference.errors import AdapterConnectionError
from octave.inference.types import Message
from tests.agent.fakes import ScriptedAdapter, final_result


def _candidate(iid: str, pid: str) -> Candidate:
    return Candidate(instance_id=iid, participant_id=pid, label=f"Agent {iid}")


def _state(*messages: Message, roster: list[Candidate] | None = None) -> DecisionState:
    return DecisionState(
        roster=list(roster) if roster else [_candidate("i_1", "p_ai1")],
        messages=list(messages),
    )


async def test_fast_path_picks_sole_candidate_when_user_spoke_last() -> None:
    adapter = ScriptedAdapter([])  # any call fails the test via queue assert
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(Message(role="user", content="Hello"))
    assert await decider.decide(state) == "p_ai1"
    assert adapter.complete_calls == []


async def test_fast_path_awaits_user_when_agent_spoke_last() -> None:
    adapter = ScriptedAdapter([])
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(
        Message(role="user", content="Hello"),
        Message(role="assistant", content="Hi"),
    )
    assert await decider.decide(state) == Decision.AWAIT_USER
    assert adapter.complete_calls == []


async def test_fast_path_empty_transcript_picks_candidate() -> None:
    adapter = ScriptedAdapter([])
    decider = LlmTurnDecider(adapter=adapter)
    assert await decider.decide(_state()) == "p_ai1"


async def test_multi_agent_valid_choice() -> None:
    roster = [_candidate("i_1", "p_ai1"), _candidate("i_2", "p_ai2")]
    adapter = ScriptedAdapter([final_result('{"next": "p_ai2"}')])
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(Message(role="user", content="Hello"), roster=roster)
    assert await decider.decide(state) == "p_ai2"
    assert len(adapter.complete_calls) == 1


async def test_multi_agent_await_user_choice() -> None:
    roster = [_candidate("i_1", "p_ai1"), _candidate("i_2", "p_ai2")]
    adapter = ScriptedAdapter([final_result('{"next": "await_user"}')])
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(Message(role="user", content="Hello"), roster=roster)
    assert await decider.decide(state) == Decision.AWAIT_USER


async def test_multi_agent_malformed_output_raises() -> None:
    roster = [_candidate("i_1", "p_ai1"), _candidate("i_2", "p_ai2")]
    adapter = ScriptedAdapter([final_result("AI1 should go!")])
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(Message(role="user", content="Hello"), roster=roster)
    with pytest.raises(DeciderChoiceError):
        await decider.decide(state)


async def test_multi_agent_out_of_roster_choice_raises() -> None:
    roster = [_candidate("i_1", "p_ai1"), _candidate("i_2", "p_ai2")]
    adapter = ScriptedAdapter([final_result('{"next": "p_ghost"}')])
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(Message(role="user", content="Hello"), roster=roster)
    with pytest.raises(DeciderChoiceError):
        await decider.decide(state)


async def test_multi_agent_adapter_error_propagates() -> None:
    roster = [_candidate("i_1", "p_ai1"), _candidate("i_2", "p_ai2")]
    adapter = ScriptedAdapter([AdapterConnectionError("engine down")])
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(Message(role="user", content="Hello"), roster=roster)
    with pytest.raises(AdapterConnectionError):
        await decider.decide(state)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/agent/test_decider.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'octave.agent.decider'`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/src/octave/agent/decider.py`:

```python
"""Turn-taking decisions (design spec 2026-09-28).

The referee, not a player: a ``TurnDecider`` reads the roster + transcript
tail and names the next speaker (or hands control to the human). The
driver owns retry and fallback policy; deciders only raise
``DeciderChoiceError`` on unusable backend output.

``LlmTurnDecider`` is the default strategy. Decision models (Laya/Jev
style) arrive behind this same protocol in a follow-up issue — the driver
never learns which backend chose.
"""

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ValidationError

from octave.agent.errors import DeciderChoiceError
from octave.inference.adapter import InferenceAdapter
from octave.inference.types import CompletionRequest, Message

__all__ = ["Candidate", "Decision", "DecisionState", "LlmTurnDecider", "TurnDecider"]


@dataclass(frozen=True)
class Candidate:
    """One agent eligible to take the next turn."""

    instance_id: str
    participant_id: str
    label: str


@dataclass(frozen=True)
class DecisionState:
    """Decider input: the candidate roster + the transcript tail."""

    roster: list[Candidate] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)


class Decision(StrEnum):
    """Sentinel vocabulary for decider output. A decision is a plain
    ``str``: either ``Decision.AWAIT_USER`` or a candidate's
    ``participant_id`` (StrEnum members are strs, so the union collapses
    cleanly)."""

    AWAIT_USER = "await_user"


class TurnDecider(Protocol):
    """Next-speaker resolution. Implementations raise
    ``DeciderChoiceError`` on malformed/out-of-roster output."""

    async def decide(self, state: DecisionState) -> str:
        """Return ``Decision.AWAIT_USER`` or a participant_id from
        ``state.roster``."""
        ...


class _Choice(BaseModel):
    """Wire shape of the multi-agent decision: exactly one JSON object."""

    next: str


class LlmTurnDecider:
    """Default strategy over the chat ``InferenceAdapter`` (local-first).

    1:1 fast path (one candidate) requires no LLM call: user spoke last →
    the candidate speaks; the sole agent spoke last → await the human.
    Multi-agent asks for ``{"next": "<participant_id>" | "await_user"}``,
    roster-validated; malformed or phantom choices raise
    ``DeciderChoiceError`` (the driver retries once, then awaits user).
    """

    def __init__(
        self, *, adapter: InferenceAdapter, model: str | None = None
    ) -> None:
        self._adapter = adapter
        self._model = model

    async def decide(self, state: DecisionState) -> str:
        if len(state.roster) == 1:
            if state.messages and state.messages[-1].role == "assistant":
                return Decision.AWAIT_USER
            return state.roster[0].participant_id
        if not state.roster:
            return Decision.AWAIT_USER
        result = await self._adapter.complete(
            CompletionRequest(model=self._model, messages=self._prompt(state))
        )
        try:
            choice = _Choice.model_validate(json.loads(result.text)).next
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DeciderChoiceError(
                f"decider output not parseable: {result.text!r}"
            ) from exc
        if choice == Decision.AWAIT_USER:
            return Decision.AWAIT_USER
        if any(candidate.participant_id == choice for candidate in state.roster):
            return choice
        raise DeciderChoiceError(f"decider chose unknown candidate: {choice!r}")

    @staticmethod
    def _prompt(state: DecisionState) -> list[Message]:
        roster = "\n".join(
            f"- {candidate.participant_id}: {candidate.label}"
            for candidate in state.roster
        )
        system = (
            "You are the turn referee for a multi-agent conversation. "
            "Decide who speaks next: one candidate participant_id, or "
            "await_user to hand control to the human. Base the choice on "
            "the conversation below. Reply with exactly one JSON object: "
            '{"next": "<participant_id>" | "await_user"}\n\n'
            f"Candidates:\n{roster}"
        )
        return [Message(role="system", content=system), *state.messages]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_decider.py -v`
Expected: all PASS (8 tests).

- [ ] **Step 5: Lint + type check, then commit**

Run: `cd backend && uv run ruff check src tests && uv run mypy src`
Expected: no errors.

```bash
git add backend/src/octave/agent/decider.py backend/tests/agent/test_decider.py
git commit -m "feat(agent): add TurnDecider protocol and LlmTurnDecider (#27)"
```

---

### Task 4: `MessageRouter` driver

**Files:**
- Create: `backend/src/octave/agent/router.py`
- Create: `backend/tests/agent/test_router.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/agent/test_router.py`:

```python
"""MessageRouter driver (design spec 2026-09-28).

Real SQLite via session_factory; spawns driven through AgentInstanceManager;
scripted decider/runner fakes (no LLM). Transcript assertions read events
back through EventStore on a fresh session.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.agent import AgentInstanceManager, AgentRegistry
from octave.agent.decider import Decision, DecisionState
from octave.agent.errors import DeciderChoiceError, NotAMemberError
from octave.agent.router import MessageRouter, StopReason
from octave.db.event_store import EventStore
from octave.db.models import Agent, Participant, Session, SessionParticipant, User
from octave.db.types import InstanceStatus
from octave.inference.types import Message

_TAG_BINDING = {"kind": "tag", "tag": "quick"}


async def _seed(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(Agent(id="a_1", name="Echo", model_binding=_TAG_BINDING))
        session.add(Agent(id="a_2", name="Second", model_binding=_TAG_BINDING))
        session.add(Session(id="s_1", created_by_user_id="u_1", status="active"))
        await session.commit()


async def _spawn(
    session_factory: async_sessionmaker[AsyncSession], agent_id: str
) -> str:
    async with session_factory() as session:
        instance = await AgentInstanceManager(session).spawn(
            agent_id=agent_id, session_id="s_1"
        )
        await session.commit()
        return instance.id


async def _invite_user(session_factory: async_sessionmaker[AsyncSession]) -> str:
    """User participant + live membership in s_1."""
    async with session_factory() as session:
        participant = Participant(id="p_u1", user_id="u_1", label="Alice")
        session.add(participant)
        await session.flush()
        session.add(
            SessionParticipant(session_id="s_1", participant_id="p_u1")
        )
        await session.commit()
    return "p_u1"


async def _participant_of(
    session_factory: async_sessionmaker[AsyncSession], agent_id: str
) -> str:
    async with session_factory() as session:
        row = await session.execute(
            select(Participant.id).where(Participant.agent_id == agent_id)
        )
        return row.scalar_one()


class ScriptedDecider:
    """Pops queued choices (Exception items raise); records each state."""

    def __init__(self, choices: list[str]) -> None:
        self._choices = list(choices)
        self.states: list[DecisionState] = []

    async def decide(self, state: DecisionState) -> str:
        self.states.append(state)
        item = self._choices.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RecordingRunner:
    """Returns queued replies (Exception items raise); records inputs."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[str, list[Message]]] = []

    async def run_turn(self, *, instance, messages: list[Message]) -> str:  # type: ignore[no-untyped-def]
        self.calls.append((instance.instance_id, list(messages)))
        item = self._replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _router(
    session: AsyncSession,
    *,
    decider: ScriptedDecider,
    runner: RecordingRunner,
    max_agent_turns: int = 4,
) -> MessageRouter:
    return MessageRouter(
        session=session,
        manager=AgentInstanceManager(session),
        registry=AgentRegistry(session),
        decider=decider,
        turn_runner=runner,
        max_agent_turns=max_agent_turns,
    )


async def _deliver(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    decider: ScriptedDecider,
    runner: RecordingRunner,
    author: str = "p_u1",
    max_agent_turns: int = 4,
):
    async with session_factory() as session:
        router = _router(
            session, decider=decider, runner=runner, max_agent_turns=max_agent_turns
        )
        outcome = await router.deliver("s_1", author_participant_id=author, content="Hello")
        await session.commit()
        return outcome


async def _transcript(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[int, str, str | None, str]]:
    async with session_factory() as session:
        events = await EventStore(session).read("s_1")
    return [
        (e.seq, e.kind, e.author_participant_id, str(e.payload.get("content", "")))
        for e in events
    ]


async def test_one_to_one_end_to_end(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    p_ai1 = await _participant_of(session_factory, "a_1")
    await _invite_user(session_factory)
    decider = ScriptedDecider([p_ai1, Decision.AWAIT_USER])
    runner = RecordingRunner(["Hi there"])
    outcome = await _deliver(session_factory, decider=decider, runner=runner)
    assert outcome.stop_reason == StopReason.AWAIT_USER
    assert len(outcome.turns) == 1
    assert outcome.turns[0].agent_id == "a_1"
    assert outcome.turns[0].seq_start == outcome.turns[0].seq_end == 2
    assert await _transcript(session_factory) == [
        (1, "user_message", "p_u1", "Hello"),
        (2, "assistant_message", p_ai1, "Hi there"),
    ]


async def test_multi_agent_handoff(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    await _spawn(session_factory, "a_2")
    p_ai1 = await _participant_of(session_factory, "a_1")
    p_ai2 = await _participant_of(session_factory, "a_2")
    await _invite_user(session_factory)
    decider = ScriptedDecider([p_ai1, p_ai2, Decision.AWAIT_USER])
    runner = RecordingRunner(["from AI1", "from AI2"])
    outcome = await _deliver(session_factory, decider=decider, runner=runner)
    assert [t.agent_id for t in outcome.turns] == ["a_1", "a_2"]
    # AI2's runner input contains U1's message AND AI1's reply
    assert runner.calls[1][1] == [
        Message(role="user", content="Hello"),
        Message(role="assistant", content="from AI1"),
    ]
    kinds = [(seq, kind) for seq, kind, _, _ in await _transcript(session_factory)]
    assert kinds == [
        (1, "user_message"),
        (2, "assistant_message"),
        (3, "assistant_message"),
    ]


async def test_empty_roster_awaits_user_immediately(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _invite_user(session_factory)
    decider = ScriptedDecider([])  # never consulted
    runner = RecordingRunner([])
    outcome = await _deliver(session_factory, decider=decider, runner=runner)
    assert outcome.stop_reason == StopReason.AWAIT_USER
    assert outcome.turns == []
    assert runner.calls == []
    assert await _transcript(session_factory) == [(1, "user_message", "p_u1", "Hello")]


async def test_runner_failure_releases_instance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    await _invite_user(session_factory)
    p_ai1 = await _participant_of(session_factory, "a_1")
    decider = ScriptedDecider([p_ai1])
    runner = RecordingRunner([RuntimeError("boom")])
    outcome = await _deliver(session_factory, decider=decider, runner=runner)
    assert outcome.stop_reason == StopReason.ERROR
    assert outcome.error == "boom"
    assert outcome.turns == []  # failed turn contributes no TurnRecord
    transcript = await _transcript(session_factory)
    assert [(seq, kind) for seq, kind, _, _ in transcript] == [
        (1, "user_message"),
        (2, "system"),
    ]
    assert "boom" in transcript[1][3]
    async with session_factory() as session:
        counts = await AgentRegistry(session).count_by_status()
    assert counts[InstanceStatus.ACTIVE] == 0  # turn released


async def test_hop_limit_stops_agent_chatter(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    await _invite_user(session_factory)
    p_ai1 = await _participant_of(session_factory, "a_1")
    decider = ScriptedDecider([p_ai1])  # one decision; loop stops at limit
    runner = RecordingRunner(["first"])
    outcome = await _deliver(
        session_factory, decider=decider, runner=runner, max_agent_turns=1
    )
    assert outcome.stop_reason == StopReason.HOP_LIMIT
    assert len(outcome.turns) == 1


async def test_decider_invalid_falls_back_to_await_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    await _invite_user(session_factory)
    decider = ScriptedDecider(
        [DeciderChoiceError("bad1"), DeciderChoiceError("bad2")]
    )
    runner = RecordingRunner([])
    outcome = await _deliver(session_factory, decider=decider, runner=runner)
    assert outcome.stop_reason == StopReason.AWAIT_USER
    assert outcome.turns == []
    assert runner.calls == []


async def test_non_member_author_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    async with session_factory() as session:
        router = _router(
            session, decider=ScriptedDecider([]), runner=RecordingRunner([])
        )
        try:
            with pytest.raises(NotAMemberError):
                await router.deliver(
                    "s_1", author_participant_id="p_ghost", content="Hello"
                )
        finally:
            await session.rollback()
    assert await _transcript(session_factory) == []  # nothing appended


async def test_registry_consistent_after_loop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    await _spawn(session_factory, "a_2")
    p_ai1 = await _participant_of(session_factory, "a_1")
    p_ai2 = await _participant_of(session_factory, "a_2")
    await _invite_user(session_factory)
    decider = ScriptedDecider([p_ai1, p_ai2, Decision.AWAIT_USER])
    runner = RecordingRunner(["one", "two"])
    await _deliver(session_factory, decider=decider, runner=runner)
    async with session_factory() as session:
        counts = await AgentRegistry(session).count_by_status()
    assert counts[InstanceStatus.ACTIVE] == 0
    assert counts[InstanceStatus.IDLE] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/agent/test_router.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'octave.agent.router'`.

- [ ] **Step 3: Write minimal implementation**

Create `backend/src/octave/agent/router.py`:

```python
"""Session-level message routing (design spec 2026-09-28, issue #27).

The driver: append the user message, then loop — decider picks an idle
instance, ``begin_turn`` claims the turn (the #25 mutex), the injected
``TurnRunner`` port produces the reply, the reply is recorded, the turn is
released — until await-user, the hop limit, or a runner failure.

Delivery is via the shared transcript (spec decision 2): there are no
per-agent queues; an agent's "message plus everything queued for it" is
the transcript through the current seq, which the runner reads as input.
Routing itself is ephemeral — ``events.target_participant_id`` stays NULL.

Follows the VaultStore convention: constructed with the caller's
AsyncSession, never commits. One ``deliver()`` is one caller-owned
transaction; the whole exchange commits atomically or not at all.
"""

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.agent.decider import Candidate, Decision, DecisionState, TurnDecider
from octave.agent.errors import DeciderChoiceError, NotAMemberError
from octave.agent.instances import AgentInstanceManager
from octave.agent.registry import AgentRegistry, RunningAgent
from octave.db.event_store import EventStore
from octave.db.models import Event, Participant, SessionParticipant
from octave.db.types import AgentStatus, EventKind, InstanceStatus
from octave.inference.types import Message

__all__ = [
    "MessageRouter",
    "RouteOutcome",
    "StopReason",
    "TurnRecord",
    "TurnRunner",
]

logger = logging.getLogger(__name__)


class StopReason(StrEnum):
    """Why ``deliver()`` returned control to the caller."""

    AWAIT_USER = "await_user"
    HOP_LIMIT = "hop_limit"
    ERROR = "error"


@dataclass(frozen=True)
class TurnRecord:
    """One completed agent turn. Events contributed by this turn
    (``assistant_message`` now; tool events when Integration #1's real
    runner appends them inside the bracket). CM #5's archival key per #25:
    (session_id, agent_id, seq_range) — never instance_id."""

    session_id: str
    agent_id: str
    instance_id: str
    seq_start: int
    seq_end: int


@dataclass(frozen=True)
class RouteOutcome:
    turns: list[TurnRecord] = field(default_factory=list)
    stop_reason: StopReason = StopReason.AWAIT_USER
    error: str | None = None


class TurnRunner(Protocol):
    """What happens inside a claimed turn. The real runner (context
    assembly + ToolLoop over the instance's model binding) is Integration
    #1's composition — mirrors the ToolExecutor/McpToolExecutor split.
    Returns the final reply text."""

    async def run_turn(
        self, *, instance: RunningAgent, messages: list[Message]
    ) -> str: ...


class MessageRouter:
    """Session-level turn-taking driver. Never commits; the caller owns
    the transaction boundary."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        manager: AgentInstanceManager,
        registry: AgentRegistry,
        decider: TurnDecider,
        turn_runner: TurnRunner,
        max_agent_turns: int = 4,
        decider_tail: int = 30,
    ) -> None:
        self._session = session
        self._manager = manager
        self._registry = registry
        self._decider = decider
        self._turn_runner = turn_runner
        self._max_agent_turns = max_agent_turns
        self._decider_tail = decider_tail
        self._events = EventStore(session)

    async def deliver(
        self, session_id: str, *, author_participant_id: str, content: str
    ) -> RouteOutcome:
        """Record the user message and drive agent turns until stop."""
        await self._require_membership(session_id, author_participant_id)
        await self._events.append(
            session_id,
            EventKind.USER_MESSAGE,
            author_participant_id=author_participant_id,
            payload={"content": content},
        )
        turns: list[TurnRecord] = []
        agent_turns = 0
        while True:
            roster = await self._roster(session_id)
            if not roster:
                return RouteOutcome(turns=turns, stop_reason=StopReason.AWAIT_USER)
            if agent_turns >= self._max_agent_turns:
                return RouteOutcome(turns=turns, stop_reason=StopReason.HOP_LIMIT)
            events = await self._events.read(session_id)
            messages = _transcript_messages(events)
            state = DecisionState(
                roster=roster, messages=messages[-self._decider_tail :]
            )
            choice = await self._decide_with_fallback(state)
            if choice == Decision.AWAIT_USER:
                return RouteOutcome(turns=turns, stop_reason=StopReason.AWAIT_USER)
            candidate = next(c for c in roster if c.participant_id == choice)
            outcome = await self._run_agent_turn(
                session_id, candidate, messages, turns
            )
            if outcome is not None:
                return outcome
            agent_turns += 1

    async def _run_agent_turn(
        self,
        session_id: str,
        candidate: Candidate,
        messages: list[Message],
        turns: list[TurnRecord],
    ) -> RouteOutcome | None:
        """Claim, run, record, release. None = turn completed normally;
        a RouteOutcome = the loop must stop (runner failure)."""
        # TurnInProgressError propagates: concurrent drivers on one
        # session is a caller bug; the mutex is fail-loud by design.
        await self._manager.begin_turn(candidate.instance_id)
        running = await self._registry.get_instance(candidate.instance_id)
        try:
            reply_text = await self._turn_runner.run_turn(
                instance=running, messages=messages
            )
        except Exception as exc:  # noqa: BLE001 — turn failures land idle
            logger.warning(
                "agent turn failed | instance=%s error=%s", candidate.instance_id, exc
            )
            await self._manager.end_turn(candidate.instance_id)
            await self._events.append(
                session_id,
                EventKind.SYSTEM,
                payload={"content": f"Agent turn failed: {exc}"},
            )
            return RouteOutcome(
                turns=turns, stop_reason=StopReason.ERROR, error=str(exc)
            )
        reply = await self._events.append(
            session_id,
            EventKind.ASSISTANT_MESSAGE,
            author_participant_id=candidate.participant_id,
            payload={"content": reply_text},
        )
        await self._manager.end_turn(candidate.instance_id)
        turns.append(
            TurnRecord(
                session_id=session_id,
                agent_id=running.agent_id,
                instance_id=candidate.instance_id,
                seq_start=reply.seq,
                seq_end=reply.seq,
            )
        )
        return None

    async def _decide_with_fallback(self, state: DecisionState) -> str:
        """One retry on DeciderChoiceError, then AWAIT_USER: a confused
        referee hands control to the human — never crashes the session."""
        for attempt in (0, 1):
            try:
                return await self._decider.decide(state)
            except DeciderChoiceError as exc:
                logger.warning(
                    "decider choice invalid | attempt=%s error=%s", attempt, exc
                )
        return Decision.AWAIT_USER

    async def _roster(self, session_id: str) -> list[Candidate]:
        """Idle instances of active definitions, joined to their
        participant identity (guaranteed by spawn; a missing participant
        row is write-path corruption — surface it loud)."""
        running = [
            record
            for record in await self._registry.list_instances(
                session_id=session_id, status=InstanceStatus.IDLE
            )
            if record.definition_status == AgentStatus.ACTIVE
        ]
        if not running:
            return []
        rows = await self._session.execute(
            select(Participant.agent_id, Participant.id).where(
                Participant.agent_id.in_([record.agent_id for record in running])
            )
        )
        participant_by_agent = dict(rows.all())
        return [
            Candidate(
                instance_id=record.instance_id,
                participant_id=participant_by_agent[record.agent_id],
                label=record.agent_name,
            )
            for record in running
        ]

    async def _require_membership(
        self, session_id: str, participant_id: str
    ) -> None:
        """App-level author gate: current membership (the events FK covers
        identity, not membership)."""
        row = await self._session.execute(
            select(SessionParticipant).where(
                SessionParticipant.session_id == session_id,
                SessionParticipant.participant_id == participant_id,
                SessionParticipant.left_at.is_(None),
            )
        )
        if row.scalar_one_or_none() is None:
            raise NotAMemberError(
                f"participant {participant_id} is not a current member of {session_id}"
            )


def _transcript_messages(events: list[Event]) -> list[Message]:
    """Transcript → chat messages. Tool/system entries are skipped with a
    debug log: they are context for humans and archival, not chat history."""
    messages: list[Message] = []
    for event in events:
        if event.kind == EventKind.USER_MESSAGE:
            messages.append(Message(role="user", content=event.payload["content"]))
        elif event.kind == EventKind.ASSISTANT_MESSAGE:
            messages.append(
                Message(role="assistant", content=event.payload["content"])
            )
        else:
            logger.debug(
                "skipping non-message event | kind=%s seq=%s", event.kind, event.seq
            )
    return messages
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_router.py -v`
Expected: all PASS (8 tests).

- [ ] **Step 5: Lint + type check, then commit**

Run: `cd backend && uv run ruff check src tests && uv run mypy src`
Expected: no errors.

```bash
git add backend/src/octave/agent/router.py backend/tests/agent/test_router.py
git commit -m "feat(agent): add session-level MessageRouter driver (#27)"
```

---

### Task 5: Public exports, TODO, full suite

**Files:**
- Modify: `backend/src/octave/agent/__init__.py`
- Modify: `backend/tests/agent/test_package.py`
- Modify: `docs/TODO.md`

- [ ] **Step 1: Write the failing test**

Modify `backend/tests/agent/test_package.py` — extend the names tuple (insert alphabetically):

```python
        "Candidate",
        "Decision",
        "DecisionState",
        "LlmTurnDecider",
        "MessageRouter",
        "RouteOutcome",
        "StopReason",
        "TurnDecider",
        "TurnRecord",
        "TurnRunner",
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/agent/test_package.py -v`
Expected: FAIL — `AssertionError: Candidate`.

- [ ] **Step 3: Implement exports**

Modify `backend/src/octave/agent/__init__.py` — add imports after the errors import:

```python
from octave.agent.decider import (
    Candidate,
    Decision,
    DecisionState,
    LlmTurnDecider,
    TurnDecider,
)
from octave.agent.router import (
    MessageRouter,
    RouteOutcome,
    StopReason,
    TurnRecord,
    TurnRunner,
)
```

Extend `__all__` (keep alphabetical):

```python
__all__ = [
    "AgentError",
    "AgentInstanceManager",
    "AgentNotFoundError",
    "AgentPausedError",
    "AgentRegistry",
    "Candidate",
    "DeciderChoiceError",
    "Decision",
    "DecisionState",
    "InstanceExistsError",
    "InstanceNotFoundError",
    "InvalidTransitionError",
    "LlmTurnDecider",
    "McpToolExecutor",
    "MessageRouter",
    "ModelBindingError",
    "NotAMemberError",
    "ResolvedModel",
    "RouteOutcome",
    "RoutingError",
    "RunningAgent",
    "SessionNotFoundError",
    "StopReason",
    "TerminalSessionError",
    "ToolError",
    "ToolExecutor",
    "ToolLoop",
    "ToolLoopLimitError",
    "ToolOutcome",
    "ToolTurn",
    "TurnDecider",
    "TurnInProgressError",
    "TurnRecord",
    "TurnRunner",
    "resolve_model",
]
```

Also update the package docstring's second paragraph: replace `"Future Agent Manager components (router, result collector) land here as additional modules; they do not exist yet."` with:

```
Manager components ship here as additional modules: instance lifecycle
(#25), registry (#26), message routing (#27). Result collection (#4) and
scheduling (#6) remain.
```

- [ ] **Step 4: Mark the roadmap item done**

Modify `docs/TODO.md` — replace line:

```markdown
- [ ] 3. Implement agent message routing (deliver messages to correct agent, broadcast when needed)
```

with:

```markdown
- [x] 3. Implement agent message routing (deliver messages to correct agent, broadcast when needed) — PR #107 (session-level `MessageRouter` driver, `TurnDecider`/`TurnRunner` ports, `EventStore` transcript write path; LLM decider default, decision-model decider deferred to follow-up)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_package.py -v`
Expected: PASS.

- [ ] **Step 6: Full suite + lint + types**

Run: `cd backend && uv run pytest -q`
Expected: all tests pass, no new failures against the pre-existing suite.

Run: `cd backend && uv run ruff check src tests && uv run mypy src`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add backend/src/octave/agent/__init__.py backend/tests/agent/test_package.py docs/TODO.md
git commit -m "feat(agent): export message routing surface, tick Agent Manager #3 (#27)"
```

---

## Deviations From Spec

- **Retry testing seam**: the spec's cross-session concurrent-append test is impossible on SQLite (a transaction's snapshot never sees other connections' commits, so the retry's re-read is stale by construction). The plan tests the retry loop deterministically via a `_StaleReadStore` subclass scripting one stale `_next_seq` value, and documents engine-dependence in `EventStore.append`'s docstring. The constraint guarantee (no duplicate seq ever lands) is unchanged.
- **`_next_seq` as an overridable method** (not an inline expression) — the seam the retry test needs; also makes the seq policy swappable if pgvector-era locking (e.g., `SELECT ... FOR UPDATE`) arrives.
- **`decider_tail` on the driver** (default 30) rather than only on the decider: the spec assigns the tail budget to `DecisionState` construction, which is the driver's job; `LlmTurnDecider` receives the already-tailed state.
