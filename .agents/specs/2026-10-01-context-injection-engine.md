# Context Injection Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the context injection engine: select standing vault context (prompt / preferences / skills) per agent via dual tagging + additive explicit assignments, and durably inject it once per (session, agent) as a `context_injection` transcript event.

**Architecture:** Read-side engine (`ContextInjector.select`) over the shipped vault + agent definitions, plus an idempotent writer (`ensure_injected`) that appends one snapshot event per agent through the shipped `EventStore`. Zero migrations: `EventKind` grows one member, `AgentAssignments` (JSON column) gains two list fields + a computed property, two pydantic payload models register in `EventStore._PAYLOAD_MODELS`. Lives in `octave/context/` (never imports `octave.agent` or `octave.inference`).

**Tech Stack:** Python 3.13, SQLAlchemy 2 async, pydantic v2, pytest + pytest-asyncio (auto mode), uv. Spec: [2026-10-01-context-injection-engine-design.md](./2026-10-01-context-injection-engine-design.md).

**Working directory for all commands:** `backend/` (from repo root: `cd backend`). Gates: `uv run pytest -q`, `uv run ruff check src tests`, `uv run mypy src`. Branch: `feature/context-injection-engine` (already checked out).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/octave/db/types.py` | modify — `EventKind.CONTEXT_INJECTION`; `AgentAssignments.tags` / `.preference_names` / `.effective_tags`; `InjectedContextItem`, `ContextInjectionPayload` |
| `src/octave/db/event_store.py` | modify — register `ContextInjectionPayload` in `_PAYLOAD_MODELS` |
| `src/octave/db/models/core.py` | modify — `assignments` docstring |
| `src/octave/context/errors.py` | modify — `AgentNotFound`, `ParticipantNotFound` |
| `src/octave/context/injection.py` | **new** — `ContextInjector`, `ContextBundle`, `InjectedItem`, `SelectionReason` |
| `src/octave/context/__init__.py` | modify — exports |
| `tests/db/test_types.py` | modify — enum / assignments / payload tests |
| `tests/context/test_injection.py` | **new** — selection matrix, durability, payload validation, router/archiver regressions |
| `tests/context/test_package.py` | modify — export list |
| `.agents/memory/decisions.md` | modify — ADR |
| `docs/TODO.md` | modify — CM #4 done |

Deviation note (vs. spec deliverables): the `context_injection` payload-validation test lands in `tests/context/test_injection.py` (self-contained seeding) rather than `tests/db/test_event_store.py`; same coverage, fewer fixture edits.

---

### Task 1: `EventKind.CONTEXT_INJECTION`

**Files:**
- Modify: `src/octave/db/types.py` (`EventKind`, ~line 35)
- Test: `tests/db/test_types.py`

- [ ] **Step 1: Write the failing test** — append to `tests/db/test_types.py`:

```python
def test_event_kind_context_injection_value_stable() -> None:
    assert EventKind.CONTEXT_INJECTION == "context_injection"
    assert EventKind("context_injection") is EventKind.CONTEXT_INJECTION
    assert str(EventKind.CONTEXT_INJECTION) == "context_injection"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/db/test_types.py -q`
Expected: FAIL — `AttributeError: CONTEXT_INJECTION` (or enum-value error).

- [ ] **Step 3: Implement** — in `src/octave/db/types.py`, add one member to `EventKind`:

```python
class EventKind(StrEnum):
    """One transcript entry's type. Values are stored verbatim in ``events.kind``."""

    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SYSTEM = "system"
    CONTEXT_INJECTION = "context_injection"
    """Harness-authored standing context injected at session start (issue #34).
    Never authored by a participant; targets one agent participant."""
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/db/test_types.py -q`
Expected: PASS (all, including pre-existing enum tests).

- [ ] **Step 5: Commit**

```bash
git add src/octave/db/types.py tests/db/test_types.py
git commit -m "feat(db): add context_injection event kind"
```

---

### Task 2: Injection payload models + EventStore validation

**Files:**
- Modify: `src/octave/db/types.py` (after `AssistantMessagePayload`)
- Modify: `src/octave/db/event_store.py` (`_PAYLOAD_MODELS`, line 34)
- Test: `tests/db/test_types.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/db/test_types.py`:

```python
def test_context_injection_payload_round_trip() -> None:
    payload = ContextInjectionPayload(
        agent_id="a_1",
        items=[
            {
                "item_id": "v_1",
                "kind": "preference",
                "name": "form-of-address",
                "content": "Call the user Momo.",
                "reason": "global",
            }
        ],
    )
    dumped = payload.model_dump()
    assert dumped["agent_id"] == "a_1"
    assert dumped["items"][0]["reason"] == "global"


def test_context_injection_payload_rejects_bad_reason() -> None:
    with pytest.raises(ValidationError):
        ContextInjectionPayload(
            agent_id="a_1",
            items=[
                {
                    "item_id": "v_1",
                    "kind": "skill",
                    "name": "n",
                    "content": "c",
                    "reason": "vibes",
                }
            ],
        )


def test_context_injection_payload_requires_agent_id() -> None:
    with pytest.raises(ValidationError):
        ContextInjectionPayload(items=[])
```

If `pytest` / `ValidationError` are not yet imported in that file, add: `import pytest` and `from pydantic import ValidationError`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/db/test_types.py -q`
Expected: FAIL — `NameError: ContextInjectionPayload` (or import error).

- [ ] **Step 3: Implement** — append to `src/octave/db/types.py` (after `AssistantMessagePayload`):

```python
SelectionReason = Literal["explicit", "global", "agent_tag"]
"""Why an item was selected (issue #34). Extension seam for CM #11:
a future ``retrieved`` member adds found-context provenance."""


class InjectedContextItem(BaseModel):
    """One injected vault item, snapshotted at injection time. ``kind`` is
    the verbatim ``vault_items.kind`` string; ``content`` is the exact prose
    the agent was given (the transcript records what was seen, immune to
    later vault edits)."""

    item_id: str
    kind: str
    name: str
    content: str
    reason: SelectionReason


class ContextInjectionPayload(BaseModel):
    """``context_injection`` event payload: what the harness told one agent
    at session start (issue #34)."""

    agent_id: str
    items: list[InjectedContextItem]
```

Add `"ContextInjectionPayload"`, `"InjectedContextItem"`, `"SelectionReason"` to `__all__` in `src/octave/db/types.py`, keeping alphabetical order.

Register validation in `src/octave/db/event_store.py`:

```python
from octave.db.types import (
    AssistantMessagePayload,
    ContextInjectionPayload,
    EventKind,
    UserMessagePayload,
)

_PAYLOAD_MODELS: dict[EventKind, type[BaseModel]] = {
    EventKind.USER_MESSAGE: UserMessagePayload,
    EventKind.ASSISTANT_MESSAGE: AssistantMessagePayload,
    EventKind.CONTEXT_INJECTION: ContextInjectionPayload,
}
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/db/test_types.py tests/db/test_event_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/octave/db/types.py src/octave/db/event_store.py tests/db/test_types.py
git commit -m "feat(db): context_injection payload models with write-path validation"
```

---

### Task 3: `AgentAssignments` — `tags`, `preference_names`, `effective_tags`

**Files:**
- Modify: `src/octave/db/types.py` (`AgentAssignments`, ~line 99)
- Modify: `src/octave/db/models/core.py` (`assignments` docstring, line 53)
- Test: `tests/db/test_types.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/db/test_types.py`:

```python
def test_assignments_new_fields_default_empty() -> None:
    assignments = AgentAssignments()
    assert assignments.tags == []
    assert assignments.preference_names == []
    assert assignments.effective_tags == []


def test_effective_tags_unions_alias_case_insensitively() -> None:
    assignments = AgentAssignments(
        tags=["Code", "python"], preference_tags=["code", "terse"]
    )
    # case-folded, order-stable dedup: "code" appears once, first position wins
    assert assignments.effective_tags == ["code", "python", "terse"]


def test_effective_tags_is_not_serialized() -> None:
    assignments = AgentAssignments(tags=["code"])
    assert "effective_tags" not in assignments.model_dump()
    assert "effective_tags" not in assignments.model_dump_json()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/db/test_types.py -q`
Expected: FAIL — no `tags` field / no `effective_tags` attribute.

- [ ] **Step 3: Implement** — replace the `AgentAssignments` class body in `src/octave/db/types.py`:

```python
class AgentAssignments(BaseModel):
    """Named vault-item references assigned to a definition.

    References are app-validated strings; dangling references are tolerated
    at resolution time (skip + warn). Link-table promotion triggers live in
    the design spec. Extra keys allowed, mirroring the ``vault_items.meta``
    convention (ADR 2026-09-20).

    Selection vocabulary (issue #34): ``tags`` is the agent's capability-tag
    set matched against item ``meta.tags``; ``preference_tags`` is a
    deprecated alias folded into :attr:`effective_tags`. ``skills`` and
    ``preference_names`` are explicit by-name selections, additive to the
    tag-matched ones.
    """

    model_config = ConfigDict(extra="allow")

    prompt: str | None = None
    skills: list[str] = Field(default_factory=list)
    preference_tags: list[str] = Field(default_factory=list)
    """DEPRECATED alias of ``tags`` (issue #34). Kept for stored JSON;
    removal is a one-line delete once no writer sets it."""
    tags: list[str] = Field(default_factory=list)
    preference_names: list[str] = Field(default_factory=list)

    @property
    def effective_tags(self) -> list[str]:
        """Case-folded, order-stable dedup union of ``tags`` and the
        deprecated ``preference_tags``. Read-only; excluded from
        serialization (plain property, not a pydantic field)."""
        seen: dict[str, None] = {}
        for tag in [*self.tags, *self.preference_tags]:
            seen.setdefault(tag.lower())
        return list(seen)
```

Update the `assignments` docstring in `src/octave/db/models/core.py` (line 53):

```python
    assignments: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    """``octave.db.types.AgentAssignments`` JSON: named vault-item references
    (prompt / skills / preference_names) and capability tags (tags, plus the
    deprecated preference_tags alias)."""
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/db/test_types.py tests/agent/test_registry.py -q`
Expected: PASS (registry pass-through tests keep passing — no field removed).

- [ ] **Step 5: Commit**

```bash
git add src/octave/db/types.py src/octave/db/models/core.py tests/db/test_types.py
git commit -m "feat(db): agent assignment tags and explicit preference names"
```

---

### Task 4: Context-plane errors

**Files:**
- Modify: `src/octave/context/errors.py`
- Test: `tests/context/test_injection.py` (created here, extended by later tasks)

- [ ] **Step 1: Write the failing test** — create `tests/context/test_injection.py`:

```python
"""Context injection engine (issue #34): selection + session-start durability.

Seeding mirrors tests/context/test_archiver.py: real SQLite via the shared
``env`` fixture (SqliteVecAdapter, session factory); vault writes go through
VaultStore (no embeddings — selection never touches the vector layer).
"""

from octave.context.errors import AgentNotFound, ParticipantNotFound


async def test_error_types_are_context_local(env) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert issubclass(AgentNotFound, Exception)
    assert issubclass(ParticipantNotFound, Exception)
    assert AgentNotFound("a_x").agent_id == "a_x"
    assert "a_x" in str(ParticipantNotFound("a_x"))
    assert AsyncSession  # keeps the import meaningful for mypy
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/context/test_injection.py -q`
Expected: FAIL — `ImportError: cannot import name 'AgentNotFound'`.

- [ ] **Step 3: Implement** — append to `src/octave/context/errors.py` and extend `__all__`:

```python
__all__ = [
    "AgentNotFound",
    "ContextError",
    "ModelBindingNotResolved",
    "ParticipantNotFound",
    "SessionNotFound",
]
```

```python
class AgentNotFound(ContextError):
    """No agents row for the requested id (issue #34). Mirrors the agent
    plane's error name with an independent type (plane ban)."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"agent {agent_id!r} not found")
        self.agent_id = agent_id


class ParticipantNotFound(ContextError):
    """The agent has no participant row: not spawned into any session.
    Fail loud rather than write an untargeted injection (issue #34)."""

    def __init__(self, agent_id: str) -> None:
        super().__init__(f"agent {agent_id!r} has no participant row")
        self.agent_id = agent_id
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/context/test_injection.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/octave/context/errors.py tests/context/test_injection.py
git commit -m "feat(context): AgentNotFound and ParticipantNotFound errors"
```

---

### Task 5: `ContextInjector.select` — the selection engine

**Files:**
- Create: `src/octave/context/injection.py`
- Test: `tests/context/test_injection.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/context/test_injection.py`. First add imports at the top of the file (merge with existing import block):

```python
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.context.injection import ContextBundle, ContextInjector, InjectedItem
from octave.db.models import Agent, Participant, Session, User
from octave.db.types import VaultKind
from octave.db.vault_store import VaultStore
```

Then the seed helpers and selection tests:

```python
async def _seed(
    env,
    *,
    sessions: dict[str, str],
    agents: dict[str, dict],
    participants: bool = True,
    users: tuple[str, ...] = ("u_1", "u_2"),
) -> None:
    """sessions: session_id -> owner user_id. agents: agent_id -> assignments."""
    _adapter, factory = env
    async with factory() as s:
        for uid in users:
            s.add(User(id=uid, display_name=uid))
        for agent_id, assignments in agents.items():
            s.add(Agent(id=agent_id, name=agent_id, assignments=dict(assignments)))
            if participants:
                s.add(Participant(id=f"p_{agent_id}", agent_id=agent_id, label=agent_id))
        for session_id, owner in sessions.items():
            s.add(Session(id=session_id, created_by_user_id=owner, status="active"))
        await s.commit()


async def _item(
    env,
    item_id: str,
    *,
    user_id: str,
    kind: VaultKind,
    name: str,
    content: str | None = None,
    tags: list[str] | None = None,
) -> None:
    _adapter, factory = env
    async with factory() as s:
        meta: dict = {} if tags is None else {"tags": tags}
        await VaultStore(_adapter, s).upsert(
            item_id=item_id,
            user_id=user_id,
            kind=kind,
            name=name,
            content=content or f"content of {name}",
            meta=meta,
        )
        await s.commit()


async def _select(env, session_id: str, agent_id: str) -> ContextBundle:
    _adapter, factory = env
    async with factory() as s:
        return await ContextInjector(s).select(session_id=session_id, agent_id=agent_id)


async def test_global_tag_injects_for_every_agent(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}, "a_2": {"tags": ["code"]}})
    await _item(env, "v_pref", user_id="u_1", kind=VaultKind.PREFERENCE, name="addr", tags=["global"])
    for agent_id in ("a_1", "a_2"):
        bundle = await _select(env, "s_1", agent_id)
        assert [(i.item_id, i.reason) for i in bundle.items] == [("v_pref", "global")]


async def test_agent_tag_match_selects_only_matching_agent(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_code": {"tags": ["code"]}, "a_cal": {}})
    await _item(env, "v_depth", user_id="u_1", kind=VaultKind.PREFERENCE, name="depth", tags=["code"])
    await _item(env, "v_skill", user_id="u_1", kind=VaultKind.SKILL, name="py", tags=["code"])
    matched = await _select(env, "s_1", "a_code")
    assert {i.item_id for i in matched.items} == {"v_depth", "v_skill"}
    assert all(i.reason == "agent_tag" for i in matched.items)
    unmatched = await _select(env, "s_1", "a_cal")
    assert unmatched.items == []


async def test_untagged_item_injects_only_when_named(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}, "a_2": {"skills": ["solo"]}})
    await _item(env, "v_solo", user_id="u_1", kind=VaultKind.SKILL, name="solo")
    assert (await _select(env, "s_1", "a_1")).items == []
    named = await _select(env, "s_1", "a_2")
    assert [(i.item_id, i.reason) for i in named.items] == [("v_solo", "explicit")]


async def test_explicit_is_additive_union(env) -> None:
    await _seed(
        env,
        sessions={"s_1": "u_1"},
        agents={"a_1": {"skills": ["named"], "preference_names": ["named-pref"]}},
    )
    await _item(env, "v_named", user_id="u_1", kind=VaultKind.SKILL, name="named")
    await _item(env, "v_global", user_id="u_1", kind=VaultKind.SKILL, name="g", tags=["global"])
    await _item(env, "v_np", user_id="u_1", kind=VaultKind.PREFERENCE, name="named-pref")
    await _item(env, "v_gp", user_id="u_1", kind=VaultKind.PREFERENCE, name="g-pref", tags=["global"])
    bundle = await _select(env, "s_1", "a_1")
    assert {i.item_id for i in bundle.items} == {"v_named", "v_global", "v_np", "v_gp"}


async def test_prompt_is_assignment_only(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {"prompt": "persona"}, "a_2": {}})
    await _item(env, "v_persona", user_id="u_1", kind=VaultKind.PROMPT, name="persona", tags=["global"])
    named = await _select(env, "s_1", "a_1")
    assert [(i.item_id, i.kind) for i in named.items] == [("v_persona", VaultKind.PROMPT)]
    other = await _select(env, "s_1", "a_2")
    assert other.items == []  # global tag does NOT pull prompts


async def test_owner_scoping_never_leaks_across_users(env) -> None:
    await _seed(env, sessions={"s_gorim": "u_1", "s_momo": "u_2"}, agents={"a_1": {}})
    await _item(env, "v_g", user_id="u_1", kind=VaultKind.PREFERENCE, name="p", tags=["global"])
    await _item(env, "v_m", user_id="u_2", kind=VaultKind.PREFERENCE, name="p", tags=["global"])
    gorim = await _select(env, "s_gorim", "a_1")
    momo = await _select(env, "s_momo", "a_1")
    assert [i.item_id for i in gorim.items] == ["v_g"]
    assert [i.item_id for i in momo.items] == ["v_m"]


async def test_tag_case_insensitivity(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {"tags": ["Code"]}})
    await _item(env, "v", user_id="u_1", kind=VaultKind.SKILL, name="s", tags=["code"])
    bundle = await _select(env, "s_1", "a_1")
    assert [i.item_id for i in bundle.items] == ["v"]


async def test_reason_precedence_dedup(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {"skills": ["dual"], "tags": ["code"]}})
    await _item(env, "v_dual", user_id="u_1", kind=VaultKind.SKILL, name="dual", tags=["global", "code"])
    bundle = await _select(env, "s_1", "a_1")
    assert [(i.item_id, i.reason) for i in bundle.items] == [("v_dual", "explicit")]


async def test_section_order_and_determinism(env) -> None:
    await _seed(
        env,
        sessions={"s_1": "u_1"},
        agents={"a_1": {"prompt": "persona", "preference_names": ["p"]}},
    )
    await _item(env, "v_skill", user_id="u_1", kind=VaultKind.SKILL, name="s", tags=["global"])
    await _item(env, "v_pref", user_id="u_1", kind=VaultKind.PREFERENCE, name="p")
    await _item(env, "v_prompt", user_id="u_1", kind=VaultKind.PROMPT, name="persona")
    expected = ["v_prompt", "v_pref", "v_skill"]
    for _ in range(3):  # deterministic across repeated runs
        assert [i.item_id for i in (await _select(env, "s_1", "a_1")).items] == expected


async def test_dangling_reference_skips_with_warning(env, caplog) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {"skills": ["ghost", "real"]}})
    await _item(env, "v_real", user_id="u_1", kind=VaultKind.SKILL, name="real")
    with caplog.at_level(logging.WARNING):
        bundle = await _select(env, "s_1", "a_1")
    assert [i.item_id for i in bundle.items] == ["v_real"]
    assert "ghost" in caplog.text


async def test_ambiguous_name_includes_all_with_warning(env, caplog) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {"skills": ["dup"]}})
    await _item(env, "v_a", user_id="u_1", kind=VaultKind.SKILL, name="dup")
    await _item(env, "v_b", user_id="u_1", kind=VaultKind.SKILL, name="dup")
    with caplog.at_level(logging.WARNING):
        bundle = await _select(env, "s_1", "a_1")
    assert {i.item_id for i in bundle.items} == {"v_a", "v_b"}
    assert "dup" in caplog.text


async def test_malformed_meta_tags_treated_as_untagged(env, caplog) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {"tags": ["code"]}})
    await _item(env, "v_bad", user_id="u_1", kind=VaultKind.SKILL, name="s", tags=["global"])
    # corrupt the tags field after the fact (write-path bypass simulation)
    _adapter, factory = env
    async with factory() as s:
        item = await s.get(VaultItem, "v_bad")
        item.meta = {"tags": "not-a-list"}
        await s.commit()
    with caplog.at_level(logging.WARNING):
        bundle = await _select(env, "s_1", "a_1")
    assert bundle.items == []


async def test_corrupt_assignments_json_tolerated(env, caplog) -> None:
    _adapter, factory = env
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    async with factory() as s:
        agent = await s.get(Agent, "a_1")
        agent.assignments = {"skills": "not-a-list"}
        await s.commit()
    with caplog.at_level(logging.WARNING):
        bundle = await _select(env, "s_1", "a_1")
    assert bundle.items == []
    assert "assignments" in caplog.text


async def test_select_raises_for_missing_session_and_agent(env) -> None:
    from octave.context.errors import AgentNotFound, SessionNotFound

    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    import pytest

    with pytest.raises(SessionNotFound):
        await _select(env, "s_missing", "a_1")
    with pytest.raises(AgentNotFound):
        await _select(env, "s_1", "a_missing")
```

Also add `VaultItem` to the models import at the top: `from octave.db.models import Agent, Participant, Session, User, VaultItem`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/context/test_injection.py -q`
Expected: FAIL — `ModuleNotFoundError: octave.context.injection` (collection error).

- [ ] **Step 3: Implement** — create `src/octave/context/injection.py`:

```python
"""Context injection engine (issue #34).

Selects standing context — the session owner's preferences and skills, by
dual tagging (reserved ``global`` tag + intersection with the agent's
``effective_tags``) additively unioned with explicit by-name assignments —
and durably injects it once per (session, agent) as a ``context_injection``
transcript event (session-start cadence; ``every_turn`` is post-1.0.0,
design spec Decision 1).

Read-side only: vault reads are direct SELECTs (VaultStore owns the WRITE
invariants; nothing here mutates vault_items). Writes one event through the
shipped EventStore. Never commits — callers own transaction boundaries.
Never imports ``octave.agent`` (plane ban, Decision 9) or
``octave.inference`` (zero model calls this item).
"""

import logging
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.context.errors import AgentNotFound, ParticipantNotFound, SessionNotFound
from octave.db.event_store import EventStore
from octave.db.models import Agent, Event, Participant, Session, VaultItem
from octave.db.types import (
    AgentAssignments,
    ContextInjectionPayload,
    EventKind,
    InjectedContextItem,
    SelectionReason,
    VaultKind,
)

__all__ = [
    "ContextBundle",
    "ContextInjector",
    "InjectedItem",
    "SelectionReason",
]

logger = logging.getLogger(__name__)

GLOBAL_TAG = "global"
"""Reserved item tag: inject for every agent (design spec Decision 3)."""

_PAGE = 100
_REASON_RANK: dict[SelectionReason, int] = {"explicit": 0, "global": 1, "agent_tag": 2}
_SECTION_ORDER: dict[VaultKind, int] = {
    VaultKind.PROMPT: 0,
    VaultKind.PREFERENCE: 1,
    VaultKind.SKILL: 2,
}


@dataclass(frozen=True)
class InjectedItem:
    """One selected vault item with its provenance."""

    item_id: str
    kind: VaultKind
    name: str
    content: str
    reason: SelectionReason


@dataclass(frozen=True)
class ContextBundle:
    """Selection result for one (session, agent): prompt → preferences →
    skills, deduped by item id (highest-precedence reason wins), then
    (created_at, id)."""

    session_id: str
    agent_id: str
    items: list[InjectedItem]


@dataclass(frozen=True)
class _Candidate:
    item: VaultItem
    reason: SelectionReason


class ContextInjector:
    """Session-start context selection + idempotent injection event write.
    Constructed with the caller's AsyncSession; never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._events = EventStore(session)

    async def select(self, *, session_id: str, agent_id: str) -> ContextBundle:
        """Pure selection: no writes, no inference, no participant needed."""
        session_row = await self._session.get(Session, session_id)
        if session_row is None:
            raise SessionNotFound(session_id)
        agent = await self._session.get(Agent, agent_id)
        if agent is None:
            raise AgentNotFound(agent_id)
        assignments = self._parse_assignments(agent)
        agent_tags = {t for t in assignments.effective_tags} - {GLOBAL_TAG}
        owner = session_row.created_by_user_id

        candidates: dict[str, _Candidate] = {}
        if assignments.prompt is not None:
            await self._by_name(
                candidates, owner, VaultKind.PROMPT, assignments.prompt, "explicit"
            )
        await self._section(
            candidates, owner, VaultKind.PREFERENCE, assignments.preference_names, agent_tags
        )
        await self._section(candidates, owner, VaultKind.SKILL, assignments.skills, agent_tags)

        ordered = sorted(
            candidates.values(),
            key=lambda c: (
                _SECTION_ORDER[c.item.kind and VaultKind(c.item.kind)],
                _REASON_RANK[c.reason],
                c.item.created_at,
                c.item.id,
            ),
        )
        return ContextBundle(
            session_id=session_id,
            agent_id=agent_id,
            items=[
                InjectedItem(
                    item_id=c.item.id,
                    kind=VaultKind(c.item.kind),
                    name=c.item.name,
                    content=c.item.content,
                    reason=c.reason,
                )
                for c in ordered
            ],
        )

    async def ensure_injected(
        self, *, session_id: str, agent_id: str
    ) -> ContextBundle | None:
        """Idempotent session-start write: the agent's participant already
        holding a ``context_injection`` event in this session short-circuits
        to None. Otherwise append exactly one snapshot event (harness-
        authored: author NULL, targeted at the agent). Never commits."""
        bundle = await self.select(session_id=session_id, agent_id=agent_id)
        participant_id = await self._agent_participant(agent_id)
        existing = await self._session.execute(
            select(Event.id)
            .where(
                Event.session_id == session_id,
                Event.kind == str(EventKind.CONTEXT_INJECTION),
                Event.target_participant_id == participant_id,
            )
            .limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            return None
        payload = ContextInjectionPayload(
            agent_id=agent_id,
            items=[
                InjectedContextItem(
                    item_id=i.item_id,
                    kind=str(i.kind),
                    name=i.name,
                    content=i.content,
                    reason=i.reason,
                )
                for i in bundle.items
            ],
        )
        await self._events.append(
            session_id,
            EventKind.CONTEXT_INJECTION,
            target_participant_id=participant_id,
            payload=payload.model_dump(),
        )
        return bundle

    def _parse_assignments(self, agent: Agent) -> AgentAssignments:
        """Lenient read (registry precedent): corrupt JSON → empty + warn."""
        try:
            return AgentAssignments.model_validate(agent.assignments or {})
        except ValidationError:
            logger.warning(
                "agent %s has invalid assignments; treating as empty", agent.id
            )
            return AgentAssignments()

    async def _agent_participant(self, agent_id: str) -> str:
        row = await self._session.execute(
            select(Participant.id).where(Participant.agent_id == agent_id)
        )
        participant_id = row.scalar_one_or_none()
        if participant_id is None:
            raise ParticipantNotFound(agent_id)
        return participant_id

    async def _corpus(self, owner: str, kind: VaultKind) -> list[VaultItem]:
        """All owner's items of one kind, paged to exhaustion (a silent cap
        would be a correctness cliff)."""
        rows: list[VaultItem] = []
        offset = 0
        while True:
            page = list(
                (
                    await self._session.execute(
                        select(VaultItem)
                        .where(
                            VaultItem.user_id == owner, VaultItem.kind == str(kind)
                        )
                        .order_by(VaultItem.created_at, VaultItem.id)
                        .limit(_PAGE)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            rows.extend(page)
            if len(page) < _PAGE:
                return rows
            offset += _PAGE

    async def _section(
        self,
        candidates: dict[str, _Candidate],
        owner: str,
        kind: VaultKind,
        names: list[str],
        agent_tags: set[str],
    ) -> None:
        for name in names:
            await self._by_name(candidates, owner, kind, name, "explicit")
        for item in await self._corpus(owner, kind):
            tags = self._tags_of(item)
            if GLOBAL_TAG in tags:
                self._add(candidates, item, "global")
            elif tags & agent_tags:
                self._add(candidates, item, "agent_tag")

    async def _by_name(
        self,
        candidates: dict[str, _Candidate],
        owner: str,
        kind: VaultKind,
        name: str,
        reason: SelectionReason,
    ) -> None:
        rows = list(
            (
                await self._session.execute(
                    select(VaultItem)
                    .where(
                        VaultItem.user_id == owner,
                        VaultItem.kind == str(kind),
                        VaultItem.name == name,
                    )
                    .order_by(VaultItem.created_at, VaultItem.id)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            logger.warning(
                "dangling assignment | kind=%s name=%r owner=%s", kind, name, owner
            )
            return
        if len(rows) > 1:
            logger.warning(
                "ambiguous name match | kind=%s name=%r ids=%s",
                kind,
                name,
                [r.id for r in rows],
            )
        for row in rows:
            self._add(candidates, row, reason)

    @staticmethod
    def _tags_of(item: VaultItem) -> set[str]:
        raw = item.meta.get("tags")
        if raw is None:
            return set()
        if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
            logger.warning(
                "vault item %s has malformed meta.tags; treating as untagged",
                item.id,
            )
            return set()
        return {t.lower() for t in raw}

    @staticmethod
    def _add(
        candidates: dict[str, _Candidate], item: VaultItem, reason: SelectionReason
    ) -> None:
        existing = candidates.get(item.id)
        if existing is None or _REASON_RANK[reason] < _REASON_RANK[existing.reason]:
            candidates[item.id] = _Candidate(item=item, reason=reason)
```

Note: the sort key `c.item.kind and VaultKind(c.item.kind)` is just `VaultKind(c.item.kind)` — write it as `VaultKind(c.item.kind)` directly (the `and` is a drafting artifact).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/context/test_injection.py -q`
Expected: PASS (all selection tests; `ensure_injected`-specific tests come in Task 6).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src tests && uv run mypy src
git add src/octave/context/injection.py tests/context/test_injection.py
git commit -m "feat(context): tag-driven context selection engine"
```

---

### Task 6: `ensure_injected` — durability tests

**Files:**
- Test: `tests/context/test_injection.py` (implementation shipped in Task 5)

- [ ] **Step 1: Write the tests** — append to `tests/context/test_injection.py` (add `Event` to the models import; add `from octave.db.types import EventKind`):

```python
async def _ensure(env, session_id: str, agent_id: str):
    _adapter, factory = env
    async with factory() as s:
        bundle = await ContextInjector(s).ensure_injected(
            session_id=session_id, agent_id=agent_id
        )
        await s.commit()
        return bundle


async def _injection_events(env) -> list[Event]:
    _adapter, factory = env
    async with factory() as s:
        rows = await s.execute(
            select(Event)
            .where(Event.kind == str(EventKind.CONTEXT_INJECTION))
            .order_by(Event.seq)
        )
        return list(rows.scalars().all())


async def test_ensure_injected_writes_one_targeted_snapshot(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    await _item(env, "v_pref", user_id="u_1", kind=VaultKind.PREFERENCE, name="addr", tags=["global"])
    bundle = await _ensure(env, "s_1", "a_1")
    assert bundle is not None
    events = await _injection_events(env)
    assert len(events) == 1
    event = events[0]
    assert event.author_participant_id is None
    assert event.target_participant_id == "p_a_1"
    assert event.payload == {
        "agent_id": "a_1",
        "items": [
            {
                "item_id": "v_pref",
                "kind": "preference",
                "name": "addr",
                "content": "content of addr",
                "reason": "global",
            }
        ],
    }


async def test_ensure_injected_is_idempotent(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    await _item(env, "v", user_id="u_1", kind=VaultKind.PREFERENCE, name="p", tags=["global"])
    assert await _ensure(env, "s_1", "a_1") is not None
    assert await _ensure(env, "s_1", "a_1") is None
    assert len(await _injection_events(env)) == 1


async def test_ensure_injected_one_event_per_agent(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}, "a_2": {}})
    await _item(env, "v", user_id="u_1", kind=VaultKind.PREFERENCE, name="p", tags=["global"])
    await _ensure(env, "s_1", "a_1")
    await _ensure(env, "s_1", "a_2")
    events = await _injection_events(env)
    assert sorted(e.target_participant_id for e in events) == ["p_a_1", "p_a_2"]


async def test_empty_selection_still_anchors_an_event(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    bundle = await _ensure(env, "s_1", "a_1")
    assert bundle is not None and bundle.items == []
    events = await _injection_events(env)
    assert len(events) == 1
    assert events[0].payload["items"] == []
    assert await _ensure(env, "s_1", "a_1") is None  # anchored even when empty


async def test_ensure_injected_requires_participant(env) -> None:
    import pytest

    from octave.context.errors import ParticipantNotFound

    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}}, participants=False)
    _adapter, factory = env
    async with factory() as s:
        with pytest.raises(ParticipantNotFound):
            await ContextInjector(s).ensure_injected(session_id="s_1", agent_id="a_1")


async def test_rollback_leaves_no_event(env) -> None:
    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    await _item(env, "v", user_id="u_1", kind=VaultKind.PREFERENCE, name="p", tags=["global"])
    _adapter, factory = env
    async with factory() as s:
        await ContextInjector(s).ensure_injected(session_id="s_1", agent_id="a_1")
        await s.rollback()
    assert await _injection_events(env) == []


async def test_injection_payload_validated_on_append(env) -> None:
    import pytest
    from pydantic import ValidationError

    from octave.db.event_store import EventStore

    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    _adapter, factory = env
    async with factory() as s:
        with pytest.raises(ValidationError):
            await EventStore(s).append(
                "s_1", EventKind.CONTEXT_INJECTION, payload={"items": []}
            )  # agent_id missing
```

- [ ] **Step 2: Run**

Run: `uv run pytest tests/context/test_injection.py -q`
Expected: PASS (implementation shipped in Task 5; these pin its durability contract). If any fail, fix `injection.py` — do not weaken the tests.

- [ ] **Step 3: Commit**

```bash
git add tests/context/test_injection.py
git commit -m "test(context): injection event durability, idempotence, rollback"
```

---

### Task 7: Regressions — router skips the kind; archiver anchors it

**Files:**
- Test: `tests/context/test_injection.py`

- [ ] **Step 1: Write the tests** — append:

```python
async def test_router_transcript_skips_injection_events(env) -> None:
    """_transcript_messages must ignore context_injection (chat history and
    the decider tail stay clean — design spec §Interaction)."""
    from octave.agent.router import _transcript_messages  # tests may import the agent plane
    from octave.db.event_store import EventStore

    await _seed(env, sessions={"s_1": "u_1"}, agents={"a_1": {}})
    _adapter, factory = env
    async with factory() as s:
        store = EventStore(s)
        await store.append("s_1", EventKind.USER_MESSAGE,
                           author_participant_id="p_a_1", payload={"content": "hi"})
        await store.append(
            "s_1", EventKind.CONTEXT_INJECTION, target_participant_id="p_a_1",
            payload={"agent_id": "a_1", "items": []},
        )
        events = await store.read("s_1")
        messages = _transcript_messages(events)
        assert [m.content for m in messages] == ["hi"]


async def test_archiver_anchors_injection_events() -> None:
    """Unauthored context_injection is an anchor: excluded from brackets,
    its prose never chunked (already verbatim in the vault)."""
    from octave.context.brackets import reconstruct_turns
    from octave.db.models import Event

    def _event(seq: int, kind: EventKind, author: str | None) -> Event:
        return Event(id=f"e{seq}", session_id="s_1", seq=seq, kind=str(kind),
                     author_participant_id=author, payload={})

    events = [
        _event(1, EventKind.CONTEXT_INJECTION, None),
        _event(2, EventKind.TOOL_CALL, "p_a1"),
        _event(3, EventKind.ASSISTANT_MESSAGE, "p_a1"),
    ]
    brackets = reconstruct_turns(events)
    assert [(b.seq_start, b.seq_end) for b in brackets] == [(2, 3)]
```

- [ ] **Step 2: Run**

Run: `uv run pytest tests/context/test_injection.py::test_router_transcript_skips_injection_events tests/context/test_injection.py::test_archiver_anchors_injection_events -q`
Expected: PASS with no src changes (router's else-branch and the bracket anchor rule already cover the new kind — these pin it).

- [ ] **Step 3: Commit**

```bash
git add tests/context/test_injection.py
git commit -m "test(context): router and archiver regressions for context_injection"
```

---

### Task 8: Package exports

**Files:**
- Modify: `src/octave/context/__init__.py`
- Modify: `tests/context/test_package.py`

- [ ] **Step 1: Write the failing test** — in `tests/context/test_package.py`, extend `test_public_names_are_exported`'s tuple to:

```python
    for name in (
        "AgentNotFound",
        "ArchiveReport",
        "ContextArchiver",
        "ContextBundle",
        "ContextInjector",
        "InjectedItem",
        "ModelBindingNotResolved",
        "ParticipantNotFound",
        "SessionNotFound",
        "TurnBracket",
        "reconstruct_turns",
    ):
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/context/test_package.py -q`
Expected: FAIL — `hasattr(context_pkg, "AgentNotFound")` false.

- [ ] **Step 3: Implement** — replace `src/octave/context/__init__.py`:

```python
"""Context Manager service layer (issues #35, #34).

Archives completed agent runs into the vault (#35) and selects/injects
standing context for agent turns (#34). Imports ``octave.db`` +
``octave.inference`` only — never ``octave.agent`` (design spec 2026-09-30,
Decision 9).
"""

from octave.context.archiver import ArchiveReport, ContextArchiver
from octave.context.brackets import TurnBracket, reconstruct_turns
from octave.context.errors import (
    AgentNotFound,
    ModelBindingNotResolved,
    ParticipantNotFound,
    SessionNotFound,
)
from octave.context.injection import ContextBundle, ContextInjector, InjectedItem

__all__ = [
    "AgentNotFound",
    "ArchiveReport",
    "ContextArchiver",
    "ContextBundle",
    "ContextInjector",
    "InjectedItem",
    "ModelBindingNotResolved",
    "ParticipantNotFound",
    "SessionNotFound",
    "TurnBracket",
    "reconstruct_turns",
]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/context -q`
Expected: PASS (quarantine tests included — `injection.py` imports nothing from `octave.agent`).

- [ ] **Step 5: Commit**

```bash
git add src/octave/context/__init__.py tests/context/test_package.py
git commit -m "feat(context): export injection engine surface"
```

---

### Task 9: Docs + full gates

**Files:**
- Modify: `.agents/memory/decisions.md`
- Modify: `docs/TODO.md`

- [ ] **Step 1: Append the ADR** to `.agents/memory/decisions.md` (after the 2026-09-30 entry):

```markdown
### 2026-10-01 — Session-Start Context Injection via Dual-Tagged Selection

**Context:** Issue #34 (CM #4): select vault context items for injection into
agent prompts. Users want standing context (form of address, preferences)
injected at conversation start; per-prompt injection deferred post-1.0.0.
Multi-user = separate sessions (each session's owner's preferences inject);
preferences/skills target agents by tag ("global" or intersection with the
agent's tags) plus additive explicit by-name assignments.

**Options Considered:** 1) read-side engine + new `context_injection`
transcript event for session-start durability; 2) stateless bundles (cannot
honor "once at the start" — the router rebuilds messages from events, so
unpersisted context vanishes); 3) `injection_rules` table (deferred by the
2026-09-20 ADR until a UI needs centralized rule management).

**Decision:** Option 1, zero migrations. `EventKind.CONTEXT_INJECTION`
(TEXT, app-validated; the 2026-09-13 ADR named this kind in advance) written
once per (session, agent): author NULL, `target_participant_id` = the agent,
payload = full snapshot (`agent_id` + per-item `item_id/kind/name/content/
reason`, validated by `ContextInjectionPayload` in the EventStore write
path). Selection: owner-scoped corpus (session owner only); item `meta.tags`
`global` → every agent; item tags ∩ agent `effective_tags` → that agent;
`assignments.skills`/`preference_names` additive explicit; prompt
assignment-only. `AgentAssignments` gains `tags`/`preference_names` +
computed `effective_tags` (case-folded dedup union; `preference_tags`
deprecated alias). Provenance `explicit > global > agent_tag` is the CM #11
found-context seam. Dangling names skip + warn; corrupt assignments → empty
+ warn.

**Rationale:** The event is the only cadence that survives the router's
per-turn transcript rebuild; unauthored events are archiver anchors, so
injected prose is never re-chunked (already verbatim in the vault). JSON
column absorbs the tag vocabulary without a migration; tag tables stay
deferred per the 2026-09-21 one-way-door analysis (selection never filters
at the ANN layer).

**Consequences:** Mid-session vault edits take effect next session
(session-start snapshot semantics; `every_turn` is the reserved fix). The
router skips the new kind (chat history/decider unaffected). `vault_tags`
promotion trigger unchanged. `preference_tags` removal is a one-line delete
once no writer sets it.
```

- [ ] **Step 2: Update `docs/TODO.md`** — Context Manager #4 line becomes:

```markdown
- [x] 4. Create context injection engine (select relevant context items based on rules/triggers) — PR #117 (dual-tag + additive-assignment selection; session-start durability via `context_injection` transcript event; `every_turn` and found-context deferred)
```

- [ ] **Step 3: Full gates**

Run from `backend/`: `uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all green. Fix any failures before committing (do not skip tests).

- [ ] **Step 4: Commit**

```bash
git add ../.agents/memory/decisions.md ../docs/TODO.md
git commit -m "docs: context injection engine ADR; mark CM #4 done"
```

- [ ] **Step 5: Push and update the draft PR**

```bash
git push origin feature/context-injection-engine
```

PR #117 body: link the spec ([design](../../blob/feature/context-injection-engine/.agents/specs/2026-10-01-context-injection-engine-design.md)) and this plan; mark ready for review once CI is green.

---

## Self-Review Notes (plan ↔ spec)

- **Coverage:** Decisions 1–10 → Tasks 1–9. Selection rule, event shape, error handling, all spec test bullets, deliverables table, and ADR all mapped. The one deviation (payload-validation test location) is recorded above.
- **Known drafting artifact:** in Task 5's sort key, write `VaultKind(c.item.kind)` (not the `c.item.kind and ...` expression) — flagged inline.
- **Type consistency:** `SelectionReason`, `InjectedItem`, `ContextBundle`, `ContextInjector.select/ensure_injected` signatures identical across Tasks 5–8; `effective_tags` defined Task 3, consumed Task 5.
