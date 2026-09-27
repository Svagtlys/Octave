# Agent Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the read-only `AgentRegistry` — typed, filterable queries over `agent_instances ⋈ agents` — as the central source of truth for running agents (issue #26).

**Architecture:** A single new module `octave/agent/registry.py` exposes `AgentRegistry` (constructed with a caller-supplied `AsyncSession`, never commits, never mutates) returning frozen `RunningAgent` read models. No new table, no migration: persistence already shipped with the #25 lifecycle model (`AgentInstanceManager` remains the only write path). Library-level only — no routes.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async ORM (`select`, `func.count`), Pydantic `TypeAdapter` for `AgentAssignments` validation, pytest + pytest-asyncio against real SQLite (shared `session_factory` fixture in `backend/tests/conftest.py`).

**Spec:** [`.agents/specs/2026-09-27-agent-registry-design.md`](2026-09-27-agent-registry-design.md) · Branch: `feature/agent-registry` · PR: #106

**Commands run from `backend/`.** Test style mirrors `tests/agent/test_instances.py`: real SQLite via `session_factory`, explicit commits, no mocks.

---

## File Structure

| File | Responsibility |
|---|---|
| Create `backend/src/octave/agent/registry.py` | `RunningAgent` read model + `AgentRegistry` (list / get / count). The entire registry. |
| Create `backend/tests/agent/test_registry.py` | All registry tests. |
| Modify `backend/src/octave/agent/__init__.py` | Export `AgentRegistry`, `RunningAgent`. |
| Modify `backend/tests/agent/test_package.py` | Assert the two new public names. |
| Modify `docs/TODO.md` | Mark Agent Manager #2 done. |

---

### Task 1: `RunningAgent` read model + `AgentRegistry.list_instances`

**Files:**
- Create: `backend/src/octave/agent/registry.py`
- Create: `backend/tests/agent/test_registry.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/agent/test_registry.py`:

```python
"""AgentRegistry read surface (design spec 2026-09-27-agent-registry).

Read-only queries over the #25 substrate: real SQLite via session_factory,
spawns driven through AgentInstanceManager (the only write path).
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.agent import AgentInstanceManager
from octave.agent.registry import AgentRegistry
from octave.db.models import Agent, Session, User
from octave.db.types import InstanceStatus

_TAG_BINDING = {"kind": "tag", "tag": "quick"}
_ASSIGNMENTS = {
    "prompt": "v_p",
    "skills": ["v_s1", "v_s2"],
    "preference_tags": ["terse"],
}


async def _seed_defs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """One user; agents a_1/a_2 active + a_3 paused; sessions s_1/s_2."""
    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(
            Agent(
                id="a_1",
                name="Octave",
                model_binding=_TAG_BINDING,
                assignments=dict(_ASSIGNMENTS),
            )
        )
        session.add(Agent(id="a_2", name="Helper", model_binding=_TAG_BINDING))
        session.add(
            Agent(
                id="a_3", name="Paused", status="paused", model_binding=_TAG_BINDING
            )
        )
        session.add(
            Session(id="s_1", created_by_user_id="u_1", status="active")
        )
        session.add(
            Session(id="s_2", created_by_user_id="u_1", status="active")
        )
        await session.commit()


async def _spawn(
    session_factory: async_sessionmaker[AsyncSession],
    agent_id: str,
    session_id: str,
) -> str:
    async with session_factory() as session:
        instance = await AgentInstanceManager(session).spawn(
            agent_id=agent_id, session_id=session_id
        )
        await session.commit()
        return instance.id


async def test_list_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        assert await AgentRegistry(session).list_instances() == []


async def test_list_returns_joined_fields(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    instance_id = await _spawn(session_factory, "a_1", "s_1")
    async with session_factory() as session:
        [running] = await AgentRegistry(session).list_instances()
    assert running.instance_id == instance_id
    assert running.agent_id == "a_1"
    assert running.agent_name == "Octave"
    assert running.definition_status == "active"
    assert running.instance_status == "idle"
    assert running.session_id == "s_1"
    assert running.assignments.prompt == "v_p"
    assert running.assignments.skills == ["v_s1", "v_s2"]
    assert running.assignments.preference_tags == ["terse"]
    assert running.created_at <= running.updated_at


async def test_list_filters_compose(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    i1 = await _spawn(session_factory, "a_1", "s_1")
    i2 = await _spawn(session_factory, "a_2", "s_1")
    i3 = await _spawn(session_factory, "a_1", "s_2")
    async with session_factory() as session:
        registry = AgentRegistry(session)
        by_agent = await registry.list_instances(agent_id="a_1")
        by_session = await registry.list_instances(session_id="s_1")
        by_status = await registry.list_instances(status=InstanceStatus.IDLE)
        combined = await registry.list_instances(agent_id="a_1", session_id="s_1")
    assert {r.instance_id for r in by_agent} == {i1, i3}
    assert {r.instance_id for r in by_session} == {i1, i2}
    assert {r.instance_id for r in by_status} == {i1, i2, i3}
    assert [r.instance_id for r in combined] == [i1]


async def test_list_ordering_deterministic(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    ids = [await _spawn(session_factory, a, s) for a, s in
           [("a_1", "s_1"), ("a_2", "s_1"), ("a_1", "s_2")]]
    async with session_factory() as session:
        rows = await AgentRegistry(session).list_instances()
    keys = [(r.created_at, r.instance_id) for r in rows]
    assert keys == sorted(keys)
    assert {r.instance_id for r in rows} == set(ids)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/agent/test_registry.py -v`
Expected: collection error — `ImportError: cannot import name 'AgentRegistry'` (module `octave.agent.registry` does not exist).

- [ ] **Step 3: Write minimal implementation**

Create `backend/src/octave/agent/registry.py`:

```python
"""Agent registry: read-only queries over running instances (issue #26).

Rows in ``agent_instances`` are the durable record of what is running
(spawn inserts, destroy deletes — design spec 2026-09-27); this module is
the source-of-truth *view*, not a parallel bookkeeping structure. No states
added, no transitions enforced: ``AgentInstanceManager`` remains the only
write path. Follows the VaultStore convention — constructed with the
caller's AsyncSession, never commits, never mutates.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.db.models import Agent, AgentInstance
from octave.db.types import AgentAssignments, AgentStatus, InstanceStatus

__all__ = ["AgentRegistry", "RunningAgent"]

logger = logging.getLogger(__name__)

_ASSIGNMENTS_ADAPTER: TypeAdapter[AgentAssignments] = TypeAdapter(AgentAssignments)


@dataclass(frozen=True)
class RunningAgent:
    """One row of the registry view: instance fields + joined definition
    fields. ``model_binding`` is deliberately absent (design spec: IDs,
    lifecycle state, assigned context — add a field if the dashboard needs
    the binding)."""

    instance_id: str
    agent_id: str
    agent_name: str
    definition_status: AgentStatus
    instance_status: InstanceStatus
    session_id: str
    assignments: AgentAssignments
    created_at: datetime
    updated_at: datetime


def _to_running_agent(instance: AgentInstance, agent: Agent) -> RunningAgent:
    """Status parses are loud (a bad enum value is DB corruption; silently
    coercing it would misreport lifecycle state). Assignments parsing is
    made lenient in Task 4 (reporting surface, not a validation gate)."""
    return RunningAgent(
        instance_id=instance.id,
        agent_id=instance.agent_id,
        agent_name=agent.name,
        definition_status=AgentStatus(agent.status),
        instance_status=InstanceStatus(instance.status),
        session_id=instance.session_id,
        assignments=_ASSIGNMENTS_ADAPTER.validate_python(agent.assignments),
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )


class AgentRegistry:
    """Read-only queries over ``agent_instances ⋈ agents``. The RESTRICT FK
    guarantees every instance has its definition row, so the join is safe;
    ``sessions`` is not joined — ``session_id`` lives on the instance row.
    Never commits; callers own transaction boundaries (``octave.db.deps``).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_instances(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
        status: InstanceStatus | None = None,
    ) -> list[RunningAgent]:
        """Filters compose with AND; ``status`` is the *instance* status
        (idle | active), not the definition's. Ordering ``created_at ASC,
        id ASC`` — deterministic even on timestamp ties."""
        stmt = (
            select(AgentInstance, Agent)
            .join(Agent, AgentInstance.agent_id == Agent.id)
            .order_by(AgentInstance.created_at, AgentInstance.id)
        )
        if agent_id is not None:
            stmt = stmt.where(AgentInstance.agent_id == agent_id)
        if session_id is not None:
            stmt = stmt.where(AgentInstance.session_id == session_id)
        if status is not None:
            stmt = stmt.where(AgentInstance.status == str(status))
        rows = await self._session.execute(stmt)
        return [_to_running_agent(inst, ag) for inst, ag in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_registry.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/agent/registry.py backend/tests/agent/test_registry.py
git commit -m "feat: add AgentRegistry.list_instances over agent_instances ⋈ agents"
```

---

### Task 2: `AgentRegistry.get_instance`

**Files:**
- Modify: `backend/src/octave/agent/registry.py`
- Modify: `backend/tests/agent/test_registry.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_registry.py` (and add `InstanceNotFoundError` to the existing `from octave.agent import AgentInstanceManager` line → `from octave.agent import AgentInstanceManager, InstanceNotFoundError` — it is re-exported by the package; also add `import pytest` at the top):

```python
async def test_get_instance_returns_joined_record(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    instance_id = await _spawn(session_factory, "a_1", "s_1")
    async with session_factory() as session:
        running = await AgentRegistry(session).get_instance(instance_id)
    assert running.instance_id == instance_id
    assert running.agent_name == "Octave"
    assert running.assignments.prompt == "v_p"


async def test_get_instance_missing_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        with pytest.raises(InstanceNotFoundError):
            await AgentRegistry(session).get_instance("nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/agent/test_registry.py -v`
Expected: 2 new tests FAIL with `AttributeError: 'AgentRegistry' object has no attribute 'get_instance'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/src/octave/agent/registry.py`, add the import (with the other `octave` imports):

```python
from octave.agent.errors import InstanceNotFoundError
```

and add the method to `AgentRegistry` after `list_instances`:

```python
    async def get_instance(self, instance_id: str) -> RunningAgent:
        """Raise on miss — same contract as the manager's ``_get``."""
        row = (
            await self._session.execute(
                select(AgentInstance, Agent)
                .join(Agent, AgentInstance.agent_id == Agent.id)
                .where(AgentInstance.id == instance_id)
            )
        ).first()
        if row is None:
            raise InstanceNotFoundError(instance_id)
        return _to_running_agent(row[0], row[1])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_registry.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/agent/registry.py backend/tests/agent/test_registry.py
git commit -m "feat: add AgentRegistry.get_instance raising InstanceNotFoundError"
```

---

### Task 3: `AgentRegistry.count_by_status`

**Files:**
- Modify: `backend/src/octave/agent/registry.py`
- Modify: `backend/tests/agent/test_registry.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/agent/test_registry.py`:

```python
async def test_count_by_status_zero_filled(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        counts = await AgentRegistry(session).count_by_status()
    assert counts == {InstanceStatus.IDLE: 0, InstanceStatus.ACTIVE: 0}


async def test_count_by_status_mixed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    await _spawn(session_factory, "a_1", "s_1")
    await _spawn(session_factory, "a_2", "s_1")
    i3 = await _spawn(session_factory, "a_1", "s_2")
    async with session_factory() as session:
        await AgentInstanceManager(session).begin_turn(i3)
        await session.commit()
    async with session_factory() as session:
        counts = await AgentRegistry(session).count_by_status()
    assert counts == {InstanceStatus.IDLE: 2, InstanceStatus.ACTIVE: 1}
```

(`InstanceStatus` is already imported in Task 1's test file header.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/agent/test_registry.py -v`
Expected: 2 new tests FAIL with `AttributeError: ... no attribute 'count_by_status'`.

- [ ] **Step 3: Write minimal implementation**

In `registry.py`, extend the sqlalchemy import to `from sqlalchemy import func, select` and add the method to `AgentRegistry`:

```python
    async def count_by_status(self) -> dict[InstanceStatus, int]:
        """Dashboard counter over all rows (no filters — this is not a
        filtered aggregate). Both enum keys present, zero-filled."""
        rows = await self._session.execute(
            select(AgentInstance.status, func.count()).group_by(
                AgentInstance.status
            )
        )
        counts = {status: 0 for status in InstanceStatus}
        for raw, total in rows:
            counts[InstanceStatus(raw)] += int(total)
        return counts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_registry.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/agent/registry.py backend/tests/agent/test_registry.py
git commit -m "feat: add AgentRegistry.count_by_status with zero-filled enum keys"
```

---

### Task 4: Lenient assignments parsing (warn + empty)

Spec §"Lenient parsing": the registry must never raise on data it merely reports. Assignments are references → tolerate; status *is* the subject matter → fail loud (already satisfied by the `AgentStatus(...)`/`InstanceStatus(...)` constructors in `_to_running_agent`; the second test below pins that).

**Files:**
- Modify: `backend/src/octave/agent/registry.py`
- Modify: `backend/tests/agent/test_registry.py`

- [ ] **Step 1: Write the failing tests**

Add `import logging` and `from sqlalchemy import update` to the test file imports. Append:

```python
async def test_corrupt_assignments_warn_and_empty(
    session_factory: async_sessionmaker[AsyncSession], caplog
) -> None:
    await _seed_defs(session_factory)
    await _spawn(session_factory, "a_1", "s_1")
    async with session_factory() as session:
        await session.execute(
            update(Agent).where(Agent.id == "a_1").values(assignments={"skills": "not-a-list"})
        )
        await session.commit()
    with caplog.at_level(logging.WARNING, logger="octave.agent.registry"):
        async with session_factory() as session:
            [running] = await AgentRegistry(session).list_instances()
    assert running.assignments.prompt is None
    assert running.assignments.skills == []
    assert running.assignments.preference_tags == []
    assert "invalid assignments" in caplog.text


async def test_bogus_instance_status_fails_loud(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    instance_id = await _spawn(session_factory, "a_1", "s_1")
    async with session_factory() as session:
        await session.execute(
            update(AgentInstance)
            .where(AgentInstance.id == instance_id)
            .values(status="bogus")
        )
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(ValueError):
            await AgentRegistry(session).list_instances()
```

Also extend the models import to `from octave.db.models import Agent, AgentInstance, Session, User`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/agent/test_registry.py -v`
Expected: `test_corrupt_assignments_warn_and_empty` FAILS (pydantic `ValidationError` escapes `list_instances`); `test_bogus_instance_status_fails_loud` PASSES (loud parse already in place — this test pins intentional behavior).

- [ ] **Step 3: Write minimal implementation**

In `registry.py`, add `ValidationError` to the pydantic import (`from pydantic import TypeAdapter, ValidationError`) and replace the body of `_to_running_agent`'s assignments line via a helper — replace the function with:

```python
def _parse_assignments(agent: Agent) -> AgentAssignments:
    """Lenient by design (reporting surface, not a validation gate):
    malformed references are a write-path bug, and a registry that raises
    because one definition row is corrupt would hide it worse. Mirrors the
    #25 dangling-reference rule — skip + warn."""
    try:
        return _ASSIGNMENTS_ADAPTER.validate_python(agent.assignments)
    except ValidationError:
        logger.warning(
            "agent %s has invalid assignments JSON; reporting empty", agent.id
        )
        return AgentAssignments()


def _to_running_agent(instance: AgentInstance, agent: Agent) -> RunningAgent:
    """Status parses stay loud (a bad enum value is DB corruption; silently
    coercing it would misreport lifecycle state)."""
    return RunningAgent(
        instance_id=instance.id,
        agent_id=instance.agent_id,
        agent_name=agent.name,
        definition_status=AgentStatus(agent.status),
        instance_status=InstanceStatus(instance.status),
        session_id=instance.session_id,
        assignments=_parse_assignments(agent),
        created_at=instance.created_at,
        updated_at=instance.updated_at,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_registry.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/agent/registry.py backend/tests/agent/test_registry.py
git commit -m "feat: lenient assignments parsing in AgentRegistry (warn + empty)"
```

---

### Task 5: Lifecycle visibility + immutability guarantees

**Files:**
- Modify: `backend/tests/agent/test_registry.py`

No implementation changes expected — these tests pin the spec's integration guarantees against the shipped manager. If one fails, the bug is real; fix `registry.py` accordingly.

- [ ] **Step 1: Write the tests**

Append to `backend/tests/agent/test_registry.py`:

```python
async def test_lifecycle_visible_in_registry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    instance_id = await _spawn(session_factory, "a_1", "s_1")
    async with session_factory() as session:
        [running] = await AgentRegistry(session).list_instances()
        assert running.instance_status == InstanceStatus.IDLE
    async with session_factory() as session:
        await AgentInstanceManager(session).begin_turn(instance_id)
        await session.commit()
    async with session_factory() as session:
        [running] = await AgentRegistry(session).list_instances()
        assert running.instance_status == InstanceStatus.ACTIVE
        assert (await AgentRegistry(session).count_by_status())[
            InstanceStatus.ACTIVE
        ] == 1
    async with session_factory() as session:
        await AgentInstanceManager(session).end_turn(instance_id)
        await session.commit()
    async with session_factory() as session:
        [running] = await AgentRegistry(session).list_instances()
        assert running.instance_status == InstanceStatus.IDLE
    async with session_factory() as session:
        assert await AgentInstanceManager(session).destroy(instance_id) is True
        await session.commit()
    async with session_factory() as session:
        registry = AgentRegistry(session)
        assert await registry.list_instances() == []
        assert (await registry.count_by_status())[InstanceStatus.IDLE] == 0
        with pytest.raises(InstanceNotFoundError):
            await registry.get_instance(instance_id)


async def test_registry_never_mutates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    await _spawn(session_factory, "a_1", "s_1")
    async with session_factory() as session:
        registry = AgentRegistry(session)
        [running] = await registry.list_instances()
        await registry.get_instance(running.instance_id)
        await registry.count_by_status()
        assert not session.new and not session.dirty and not session.deleted
    async with session_factory() as session:
        assert len(await AgentRegistry(session).list_instances()) == 1
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/test_registry.py -v`
Expected: 12 passed. (If these fail, do not paper over — the spec's integration contract is broken; investigate `registry.py`.)

- [ ] **Step 3: Commit**

```bash
git add backend/tests/agent/test_registry.py
git commit -m "test: pin registry lifecycle visibility and immutability guarantees"
```

---

### Task 6: Package exports

**Files:**
- Modify: `backend/src/octave/agent/__init__.py`
- Modify: `backend/tests/agent/test_package.py`

- [ ] **Step 1: Write the failing test**

In `backend/tests/agent/test_package.py`, add `"AgentRegistry"` after `"AgentPausedError"` and `"RunningAgent"` after `"ResolvedModel"` in the `test_public_names_are_exported` tuple.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/agent/test_package.py -v`
Expected: `test_public_names_are_exported` FAILS with `AssertionError: AgentRegistry`.

- [ ] **Step 3: Write minimal implementation**

In `backend/src/octave/agent/__init__.py`, add after the `loop` import line:

```python
from octave.agent.registry import AgentRegistry, RunningAgent
```

and add `"AgentRegistry"` (after `"AgentPausedError"`) and `"RunningAgent"` (after `"ResolvedModel"`) to `__all__`. Update the module docstring sentence "Future Agent Manager components (registry, router) land here" → "Future Agent Manager components (router, result collector) land here".

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/agent/ -v`
Expected: all agent tests pass, including `test_no_sdk_imports` (registry imports no `openai`/`mcp`).

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/agent/__init__.py backend/tests/agent/test_package.py
git commit -m "feat: export AgentRegistry and RunningAgent from octave.agent"
```

---

### Task 7: Roadmap checkbox + full verification

**Files:**
- Modify: `docs/TODO.md`

- [ ] **Step 1: Update the roadmap**

In `docs/TODO.md`, change Agent Manager item 2 from:

```markdown
- [ ] 2. Build agent registry (track running agents, their IDs, status, and assigned context)
```

to:

```markdown
- [x] 2. Build agent registry (track running agents, their IDs, status, and assigned context) — PR #106 (read-only `AgentRegistry` over `agent_instances ⋈ agents`; no new table)
```

- [ ] **Step 2: Run the full backend suite**

Run: `cd backend && uv run pytest -v`
Expected: all tests pass (registry suite green; no regressions).

- [ ] **Step 3: Run lint/type checks**

Run: `cd backend && uv run ruff check . && uv run mypy src/octave/agent/registry.py`
Expected: no errors. (If the repo's ruff/mypy config differs, use whatever `pyproject.toml` defines; fix any findings.)

- [ ] **Step 4: Commit and push**

```bash
git add docs/TODO.md
git commit -m "docs: mark Agent Manager #2 (agent registry) done"
git push
```

---

## Self-Review Notes

- **Spec coverage:** read model (Task 1), list + filters + ordering (Task 1), get + raise (Task 2), count zero-filled (Task 3), lenient assignments / loud status asymmetry (Task 4), lifecycle visibility / no-mutation / destroy-gone (Task 5), exports (Task 6), roadmap (Task 7). Non-goals (routes, vault resolution, pagination) have no tasks — correct.
- **Type consistency:** `RunningAgent` fields, method names (`list_instances` / `get_instance` / `count_by_status`), and `InstanceNotFoundError(instance_id)` usage match the spec's interface block verbatim across all tasks.
- **No placeholders:** every code step contains full code; every command has expected output.
