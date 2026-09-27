# Agent Lifecycle Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the agent definition/instance split: `AgentStatus`/`InstanceStatus`/`ModelBinding`/`AgentAssignments` vocabulary, the `agent_instances` table, the `agents` column rework with data migration, and the `AgentInstanceManager` enforcing the spawn → idle ⇄ active → destroy lifecycle.

**Architecture:** Definitions (`agents`) are persistent config with a definition-level `active|paused` gate. Instances (`agent_instances`) are ephemeral rows binding one definition to one session, existing only while they can take turns — hard-deleted on removal, carrying no context (the session transcript and the vault own all context). `AgentInstanceManager` in `octave.agent` is the single write path, following the `VaultStore` convention: caller-supplied `AsyncSession`, never commits.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async ORM, Pydantic v2 (discriminated unions), Alembic (SQLite), pytest + pytest-asyncio (`asyncio_mode = "auto"`), ruff, mypy strict.

**Spec:** [`.agents/specs/2026-09-27-agent-lifecycle-model-design.md`](./2026-09-27-agent-lifecycle-model-design.md)
**Branch:** `feature/agent-lifecycle-model` · **Draft PR:** https://github.com/Svagtlys/Octave/pull/105

---

## Conventions (read before starting)

- All commands run from `backend/` (e.g. `uv run pytest tests/db/test_types.py -v`).
- Tests hit a **real temp-file SQLite DB** via the `session_factory` fixture — no mocks (see `tests/db/conftest.py`).
- Enum-ish columns are `TEXT` with app-level `StrEnum` validation; **no DB `CHECK`** on status values (ADR 2026-09-13).
- Every constraint carries an explicit name (`uq_…`, `pk_…`) — see `models/base.py` docstring.
- The manager **never commits**; tests commit explicitly.
- `octave.agent` must never import `openai` or `mcp` (AST guard in `tests/agent/test_package.py`). `octave.db` imports are allowed.
- IDs: `uuid.uuid4().hex`, app-generated (house convention; the spec says "ULID" — see Deviations).

## Deviations from spec (recorded deliberately)

1. **ULID → uuid4 hex.** The shipped convention is uuid4 hex (`models/__init__.py` docstring). Nothing consumes lexicographic ordering.
2. **Tag resolution lands at turn start, not spawn.** Spec §4 allows "at spawn (or first turn)". The tag→model lookup API doesn't exist yet (Inference roadmap #7), so `resolve_model()` ships as a pure function with an injected `tag_lookup`, ready for the turn runner. Spawn still fails loud on a **missing or malformed** binding.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `backend/src/octave/db/types.py` | Modify | Add `AgentStatus`, `InstanceStatus`, `TagModelBinding`, `ExplicitModelBinding`, `ModelBinding`, `AgentAssignments` |
| `backend/src/octave/db/models/core.py` | Modify | `Agent`: `model_binding` JSON replaces `model_tag`; add `assignments` JSON |
| `backend/src/octave/db/models/instances.py` | Create | `AgentInstance` ORM model |
| `backend/src/octave/db/models/__init__.py` | Modify | Re-export `AgentInstance` |
| `backend/src/octave/db/migrations/versions/b7c3f1a2d9e4_agent_definition_rework.py` | Create | `agents`: add `model_binding`/`assignments`, copy `model_tag`, drop `model_tag` |
| `backend/src/octave/db/migrations/versions/c9d4e2f6a1b8_agent_instances.py` | Create | Create `agent_instances` |
| `backend/src/octave/db/migrations/__init__.py` | Modify | `upgrade()` gains optional `revision` param |
| `backend/src/octave/agent/errors.py` | Modify | Lifecycle error classes |
| `backend/src/octave/agent/instances.py` | Create | `AgentInstanceManager`, `resolve_model`, `ResolvedModel` |
| `backend/src/octave/agent/__init__.py` | Modify | Re-export new public names |
| `backend/tests/conftest.py` | Create | Move `engine`/`session_factory` fixtures up from `tests/db/` so `tests/agent/` can use them |
| `backend/tests/db/conftest.py` | Modify | Remove moved fixtures (inherit from parent) |
| `backend/tests/db/test_types.py` | Modify | Vocabulary tests |
| `backend/tests/db/test_models.py` | Modify | Agent column tests + instance constraint tests |
| `backend/tests/db/test_migrations.py` | Modify | `EXPECTED_TABLES`, data-migration test |
| `backend/tests/agent/test_instances.py` | Create | Manager transition/mutex/pause/reconcile tests |
| `backend/tests/agent/test_package.py` | Modify | Public-name list |

Every task ends green: commit only when the **full backend suite** plus `ruff check .` and `mypy src` pass.

---

## Task 1: DB vocabulary types

**Files:**
- Modify: `backend/src/octave/db/types.py`
- Test: `backend/tests/db/test_types.py`

- [ ] **Step 1: Commit the design docs** (deferred from architect mode)

```bash
git add .agents/specs/2026-09-27-agent-lifecycle-model-design.md .agents/specs/2026-09-27-agent-lifecycle-model.md
git commit -m "docs: agent lifecycle model design and implementation plan (#25)"
```

- [ ] **Step 2: Write the failing tests** — append to `backend/tests/db/test_types.py`

```python
from pydantic import TypeAdapter, ValidationError

from octave.db.types import (
    AgentAssignments,
    AgentStatus,
    InstanceStatus,
    ModelBinding,
)


def test_agent_status_values() -> None:
    assert AgentStatus.ACTIVE == "active"
    assert AgentStatus.PAUSED == "paused"


def test_instance_status_values() -> None:
    assert InstanceStatus.IDLE == "idle"
    assert InstanceStatus.ACTIVE == "active"


def test_model_binding_tag_form_parses() -> None:
    binding = TypeAdapter(ModelBinding).validate_python({"kind": "tag", "tag": "quick"})
    assert binding.kind == "tag"
    assert binding.tag == "quick"


def test_model_binding_explicit_form_parses() -> None:
    binding = TypeAdapter(ModelBinding).validate_python(
        {"kind": "explicit", "adapter": "openai", "model": "llama3"}
    )
    assert binding.kind == "explicit"
    assert binding.adapter == "openai"
    assert binding.model == "llama3"


def test_model_binding_unknown_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ModelBinding).validate_python({"kind": "magic"})


def test_model_binding_explicit_requires_model() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ModelBinding).validate_python({"kind": "explicit", "adapter": "openai"})


def test_assignments_defaults_are_empty() -> None:
    assignments = AgentAssignments()
    assert assignments.prompt is None
    assert assignments.skills == []
    assert assignments.preference_tags == []


def test_assignments_allow_extra_keys() -> None:
    assignments = AgentAssignments.model_validate({"workflow": "vi_1"})
    assert assignments.workflow == "vi_1"
```

Note: `pytest` is already imported at the top of `test_types.py` if present; if not, add `import pytest`.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/db/test_types.py -v`
Expected: FAIL — `ImportError: cannot import name 'AgentStatus'`

- [ ] **Step 4: Implement** — in `backend/src/octave/db/types.py`

Extend imports at the top of the file to include `Annotated`, `Literal` (from `typing`), `ConfigDict`, `Field` (from `pydantic`), and extend `__all__` with the new names:

```python
__all__ = [
    "AgentAssignments",
    "AgentStatus",
    "AssistantMessagePayload",
    "EventKind",
    "ExplicitModelBinding",
    "InstanceStatus",
    "ModelBinding",
    "TagModelBinding",
    "UserMessagePayload",
    "VaultKind",
    "VectorHit",
]
```

Add after `VaultKind`:

```python
class AgentStatus(StrEnum):
    """Definition-level gate for spawning and turns. Stored verbatim in
    ``agents.status``. ``paused`` means "do not run this agent anywhere" —
    session-scoped refusal is turn policy, not agent status (design spec)."""

    ACTIVE = "active"
    PAUSED = "paused"


class InstanceStatus(StrEnum):
    """Runtime state of one agent instance. Stored verbatim in
    ``agent_instances.status``. There is no ``failed``: turn failures are
    events in the session, and sessions own ``failed``."""

    IDLE = "idle"
    ACTIVE = "active"


class TagModelBinding(BaseModel):
    """Capability-tag binding, resolved at turn start via model tagging."""

    kind: Literal["tag"]
    tag: str


class ExplicitModelBinding(BaseModel):
    """Direct provider-model pair; adapter existence validated at
    definition-save time (follow-up issue), not at turn start."""

    kind: Literal["explicit"]
    adapter: str
    model: str


ModelBinding = Annotated[
    TagModelBinding | ExplicitModelBinding, Field(discriminator="kind")
]


class AgentAssignments(BaseModel):
    """Named vault-item references assigned to a definition.

    References are app-validated strings; dangling references are tolerated
    at resolution time (skip + warn). Link-table promotion triggers live in
    the design spec. Extra keys allowed, mirroring the ``vault_items.meta``
    convention (ADR 2026-09-20)."""

    model_config = ConfigDict(extra="allow")

    prompt: str | None = None
    skills: list[str] = Field(default_factory=list)
    preference_tags: list[str] = Field(default_factory=list)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/db/test_types.py -v`
Expected: all PASS (8 new + existing)

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run mypy src && uv run pytest -q
git add src/octave/db/types.py tests/db/test_types.py
git commit -m "feat(db): agent lifecycle vocabulary types (#25)"
```

---

## Task 2: Agent definition rework + migration

**Files:**
- Modify: `backend/src/octave/db/models/core.py:35-52`
- Create: `backend/src/octave/db/migrations/versions/b7c3f1a2d9e4_agent_definition_rework.py`
- Test: `backend/tests/db/test_models.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/db/test_models.py`

```python
async def test_agent_binding_and_assignments_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            Agent(
                id="a_1",
                name="Octave",
                model_binding={"kind": "tag", "tag": "quick"},
                assignments={"skills": ["vi_1"]},
            )
        )
        await session.commit()
        agent = await session.get(Agent, "a_1")
        assert agent is not None
        assert agent.model_binding == {"kind": "tag", "tag": "quick"}
        assert agent.assignments == {"skills": ["vi_1"]}


async def test_agent_assignments_default_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(Agent(id="a_1", name="Octave"))
        await session.commit()
        agent = await session.get(Agent, "a_1")
        assert agent is not None and agent.assignments == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/db/test_models.py -v -k "binding or assignments"`
Expected: FAIL — `TypeError: 'model_binding' is an invalid keyword argument for Agent`

- [ ] **Step 3: Implement the model change** — in `backend/src/octave/db/models/core.py`

Add `from typing import Any` to imports and `JSON` to the `sqlalchemy` import list. Replace the `Agent` body columns (`model_tag` at lines 43-48) with:

```python
class Agent(Base):
    """The Agent Registry. An agent is NOT a subtype of user: it has a model
    binding, a lifecycle, and it never owns data (``sessions``/``vault_items``
    point at ``users``)."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    model_binding: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    """``octave.db.types.ModelBinding`` JSON: tag or explicit provider-model
    pair. App-validated at write time; NULL means "unusable" — spawn fails
    loud on it."""

    assignments: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    """``octave.db.types.AgentAssignments`` JSON: named vault-item references
    (prompt / skills / preference_tags)."""

    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    """``active | paused`` (AgentStatus, app-validated) — definition-level
    gate; instance lifecycle lives in ``agent_instances``."""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
```

- [ ] **Step 4: Fix existing tests referencing `model_tag`**

In `backend/tests/db/test_models.py` line 25, replace `model_tag="quick"` with `model_binding={"kind": "tag", "tag": "quick"}`.

Run: `uv run pytest tests/db/test_models.py -v`
Expected: all PASS (tests use `Base.metadata.create_all`, so they pass before the migration exists)

- [ ] **Step 5: Write the Alembic revision** — create `backend/src/octave/db/migrations/versions/b7c3f1a2d9e4_agent_definition_rework.py`

```python
"""agent definition rework: model_binding, assignments

Revision ID: b7c3f1a2d9e4
Revises: 4bf075ee2ede
Create Date: 2026-09-27

SQLite table rebuilds run via batch_alter_table. Two batch contexts (add,
then drop) bracket the data copy: batch queues its ops until context exit,
so the copy cannot be interleaved inside one batch.
"""
from collections.abc import Sequence
import json

from alembic import op
import sqlalchemy as sa

# Autogenerate renders the UTCDateTime TypeDecorator by qualified name.
import octave.db.models.base


revision: str = 'b7c3f1a2d9e4'
down_revision: str | None = '4bf075ee2ede'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('agents') as batch_op:
        batch_op.add_column(sa.Column('model_binding', sa.JSON(), nullable=True))
        batch_op.add_column(
            sa.Column(
                'assignments',
                sa.JSON(),
                nullable=False,
                server_default=sa.text("('{}')"),
            )
        )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, model_tag FROM agents WHERE model_tag IS NOT NULL")
    ).all()
    for row in rows:
        bind.execute(
            sa.text("UPDATE agents SET model_binding = :binding WHERE id = :id"),
            {"binding": json.dumps({"kind": "tag", "tag": row.model_tag}), "id": row.id},
        )
    with op.batch_alter_table('agents') as batch_op:
        batch_op.drop_column('model_tag')


def downgrade() -> None:
    with op.batch_alter_table('agents') as batch_op:
        batch_op.add_column(sa.Column('model_tag', sa.Text(), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, model_binding FROM agents")).all()
    for row in rows:
        if not row.model_binding:
            continue
        binding = json.loads(row.model_binding)
        if binding.get("kind") == "tag":
            bind.execute(
                sa.text("UPDATE agents SET model_tag = :tag WHERE id = :id"),
                {"tag": binding.get("tag"), "id": row.id},
            )
    with op.batch_alter_table('agents') as batch_op:
        batch_op.drop_column('assignments')
        batch_op.drop_column('model_binding')
```

- [ ] **Step 6: Verify migrations run and schema matches models**

Run: `uv run pytest tests/db/test_migrations.py -v`
Expected: all PASS — `test_migrated_schema_matches_models` catches any drift between the new revision and the ORM.

- [ ] **Step 7: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run mypy src && uv run pytest -q
git add src/octave/db/models/core.py src/octave/db/migrations/versions/b7c3f1a2d9e4_agent_definition_rework.py tests/db/test_models.py
git commit -m "feat(db): replace model_tag with model_binding/assignments JSON (#25)"
```

---

## Task 3: AgentInstance model + migration

**Files:**
- Create: `backend/src/octave/db/models/instances.py`
- Modify: `backend/src/octave/db/models/__init__.py`
- Create: `backend/src/octave/db/migrations/versions/c9d4e2f6a1b8_agent_instances.py`
- Modify: `backend/tests/db/test_migrations.py:12-21`
- Test: `backend/tests/db/test_models.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/db/test_models.py`

Add `AgentInstance` to the `from octave.db.models import (...)` list at the top, then:

```python
async def _seed_agent_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """One user, one agent, one session — no membership yet."""
    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(Agent(id="a_1", name="Octave"))
        session.add(Session(id="s_1", created_by_user_id="u_1"))
        await session.commit()


async def test_instance_status_defaults_idle(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agent_session(session_factory)
    async with session_factory() as session:
        session.add(AgentInstance(id="i_1", agent_id="a_1", session_id="s_1"))
        await session.commit()
        instance = await session.get(AgentInstance, "i_1")
        assert instance is not None and instance.status == "idle"


async def test_instance_unique_per_agent_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agent_session(session_factory)
    async with session_factory() as session:
        session.add(AgentInstance(id="i_1", agent_id="a_1", session_id="s_1"))
        session.add(AgentInstance(id="i_2", agent_id="a_1", session_id="s_1"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_agent_delete_restricted_while_instance_exists(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agent_session(session_factory)
    async with session_factory() as session:
        session.add(AgentInstance(id="i_1", agent_id="a_1", session_id="s_1"))
        await session.commit()
    async with session_factory() as session:
        agent = await session.get(Agent, "a_1")
        await session.delete(agent)
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_session_delete_cascades_instances(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_agent_session(session_factory)
    async with session_factory() as session:
        session.add(AgentInstance(id="i_1", agent_id="a_1", session_id="s_1"))
        await session.commit()
    async with session_factory() as session:
        sess = await session.get(Session, "s_1")
        await session.delete(sess)
        await session.commit()
    async with session_factory() as session:
        assert await session.get(AgentInstance, "i_1") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/db/test_models.py -v -k instance`
Expected: FAIL — `ImportError: cannot import name 'AgentInstance'`

- [ ] **Step 3: Implement the model** — create `backend/src/octave/db/models/instances.py`

```python
"""Agent runtime instances: ephemeral bindings of a definition to a session.

Rows exist only while the instance can take turns; destroy is a hard delete
(design spec 2026-09-27). No context lives on this table: live context is
the session transcript, long-term memory is the vault.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from octave.db.models.base import Base, UTCDateTime, utcnow

__all__ = ["AgentInstance"]


class AgentInstance(Base):
    """One live binding of an agent definition to one session.

    ``agent_id`` is RESTRICT: deleting a definition with live instances is
    the app-guarded door recorded in the design spec. ``session_id`` is
    CASCADE: a session ending destroys its instances.
    """

    __tablename__ = "agent_instances"
    __table_args__ = (
        UniqueConstraint(
            "agent_id", "session_id", name="uq_agent_instances_agent_session"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="idle")
    """``idle | active`` (InstanceStatus, app-validated; TEXT by house
    style — cf. events.kind rationale)."""
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
```

- [ ] **Step 4: Re-export** — in `backend/src/octave/db/models/__init__.py`

```python
from octave.db.models.instances import AgentInstance
```

and add `"AgentInstance",` to `__all__` (alphabetical: after `"Agent",`).

- [ ] **Step 5: Write the Alembic revision** — create `backend/src/octave/db/migrations/versions/c9d4e2f6a1b8_agent_instances.py`

```python
"""agent instances table

Revision ID: c9d4e2f6a1b8
Revises: b7c3f1a2d9e4
Create Date: 2026-09-27
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# Autogenerate renders the UTCDateTime TypeDecorator by qualified name.
import octave.db.models.base


revision: str = 'c9d4e2f6a1b8'
down_revision: str | None = 'b7c3f1a2d9e4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('agent_instances',
    sa.Column('id', sa.Text(), nullable=False),
    sa.Column('agent_id', sa.Text(), nullable=False),
    sa.Column('session_id', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('created_at', octave.db.models.base.UTCDateTime(), nullable=False),
    sa.Column('updated_at', octave.db.models.base.UTCDateTime(), nullable=False),
    sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('agent_id', 'session_id', name='uq_agent_instances_agent_session')
    )


def downgrade() -> None:
    op.drop_table('agent_instances')
```

- [ ] **Step 6: Update EXPECTED_TABLES** — in `backend/tests/db/test_migrations.py`, add `"agent_instances",` to the `EXPECTED_TABLES` set (lines 12-21).

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/db/test_models.py tests/db/test_migrations.py -v`
Expected: all PASS (4 new instance tests; schema-equality test covers the new table)

- [ ] **Step 8: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run mypy src && uv run pytest -q
git add src/octave/db/models/instances.py src/octave/db/models/__init__.py src/octave/db/migrations/versions/c9d4e2f6a1b8_agent_instances.py tests/db/test_models.py tests/db/test_migrations.py
git commit -m "feat(db): agent_instances table with unique binding and FK policy (#25)"
```

---

## Task 4: `upgrade(revision=)` + data-migration test

**Files:**
- Modify: `backend/src/octave/db/migrations/__init__.py:40-51`
- Test: `backend/tests/db/test_migrations.py`

- [ ] **Step 1: Write the failing test** — append to `backend/tests/db/test_migrations.py`

Add `import json` and `from sqlalchemy import text` to imports, then:

```python
def test_model_tag_migrates_to_tag_binding(tmp_path: Path) -> None:
    """Old ``model_tag`` values survive as tag-form ``model_binding``."""
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    upgrade(url, revision="4bf075ee2ede")  # pre-rework schema
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO agents (id, name, model_tag, status, created_at) "
                "VALUES ('a_1', 'Octave', 'quick', 'active', '2026-01-01 00:00:00')"
            )
        )
    engine.dispose()
    upgrade(url)  # to head
    engine = create_engine(url)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT model_binding FROM agents WHERE id = 'a_1'")
        ).one()
    engine.dispose()
    assert json.loads(row.model_binding) == {"kind": "tag", "tag": "quick"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/db/test_migrations.py::test_model_tag_migrates_to_tag_binding -v`
Expected: FAIL — `TypeError: upgrade() got an unexpected keyword argument 'revision'`

- [ ] **Step 3: Implement** — in `backend/src/octave/db/migrations/__init__.py`, replace `upgrade`:

```python
def upgrade(sync_url: str, revision: str = "head") -> None:
    """Apply migrations to ``revision`` (default: head). Idempotent.

    Raises ``DbMigrationError`` on failure; Alembic's own exceptions never
    escape this module. The ``revision`` parameter exists for tests that
    seed data at an older schema revision.
    """
    logger.info("applying migrations to %s (target %s)", sync_url, revision)
    try:
        _alembic_upgrade(_config(sync_url), revision)
    except Exception as exc:  # Alembic raises broad; translate at the boundary
        logger.exception("migration failed for %s", sync_url)
        raise DbMigrationError(f"migration failed: {exc}") from exc
    logger.info("migrations applied to %s", sync_url)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/db/test_migrations.py -v`
Expected: all PASS

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run mypy src && uv run pytest -q
git add src/octave/db/migrations/__init__.py tests/db/test_migrations.py
git commit -m "feat(db): upgrade accepts target revision; pin model_tag data migration (#25)"
```

---

## Task 5: Manager spawn

**Files:**
- Create: `backend/tests/conftest.py`
- Modify: `backend/tests/db/conftest.py`
- Modify: `backend/src/octave/agent/errors.py`
- Create: `backend/src/octave/agent/instances.py`
- Test: `backend/tests/agent/test_instances.py`

- [ ] **Step 1: Move DB fixtures up one level**

Create `backend/tests/conftest.py` containing **exactly the current contents of `backend/tests/db/conftest.py`** (the `_make_async_creator` helper plus the `engine` and `session_factory` fixtures, docstring included). Then empty `backend/tests/db/conftest.py` to a single comment line:

```python
# Fixtures live in tests/conftest.py so tests/agent/ shares them.
```

Run: `uv run pytest tests/db -q`
Expected: all PASS (fixtures inherited from the parent conftest)

- [ ] **Step 2: Write the failing tests** — create `backend/tests/agent/test_instances.py`

```python
"""AgentInstanceManager lifecycle (design spec 2026-09-27).

One write path, real SQLite, explicit commits — mirrors VaultStore testing.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.agent.errors import (
    AgentNotFoundError,
    AgentPausedError,
    InstanceExistsError,
    ModelBindingError,
    SessionNotFoundError,
    TerminalSessionError,
)
from octave.agent.instances import AgentInstanceManager
from octave.db.models import (
    Agent,
    AgentInstance,
    Participant,
    Session,
    SessionParticipant,
    User,
)

_TAG_BINDING = {"kind": "tag", "tag": "quick"}


async def _seed_defs(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    agent_status: str = "active",
    session_status: str = "active",
    model_binding: dict[str, str] | None = _TAG_BINDING,
) -> None:
    """One user, one agent (a_1), one session (s_1); no membership."""
    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(
            Agent(id="a_1", name="Octave", status=agent_status, model_binding=model_binding)
        )
        session.add(Session(id="s_1", created_by_user_id="u_1", status=session_status))
        await session.commit()


async def test_spawn_creates_idle_instance_and_membership(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        manager = AgentInstanceManager(session)
        instance = await manager.spawn(agent_id="a_1", session_id="s_1")
        await session.commit()
        assert instance.status == "idle"
        assert instance.agent_id == "a_1" and instance.session_id == "s_1"
    async with session_factory() as session:
        members = (await session.execute(select(SessionParticipant))).scalars().all()
        assert len(members) == 1
        participant = await session.get(Participant, members[0].participant_id)
        assert participant is not None and participant.agent_id == "a_1"
        assert participant.label == "Octave"


async def test_spawn_reuses_existing_participant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        session.add(Participant(id="p_agent", agent_id="a_1", label="Octave"))
        await session.commit()
    async with session_factory() as session:
        manager = AgentInstanceManager(session)
        await manager.spawn(agent_id="a_1", session_id="s_1")
        await session.commit()
    async with session_factory() as session:
        participants = (await session.execute(select(Participant))).scalars().all()
        assert len(participants) == 1


async def test_spawn_paused_agent_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory, agent_status="paused")
    async with session_factory() as session:
        with pytest.raises(AgentPausedError):
            await AgentInstanceManager(session).spawn(agent_id="a_1", session_id="s_1")


async def test_spawn_terminal_session_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory, session_status="completed")
    async with session_factory() as session:
        with pytest.raises(TerminalSessionError):
            await AgentInstanceManager(session).spawn(agent_id="a_1", session_id="s_1")


async def test_spawn_missing_agent_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        with pytest.raises(AgentNotFoundError):
            await AgentInstanceManager(session).spawn(agent_id="ghost", session_id="s_1")


async def test_spawn_missing_session_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        with pytest.raises(SessionNotFoundError):
            await AgentInstanceManager(session).spawn(agent_id="a_1", session_id="ghost")


async def test_spawn_missing_binding_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory, model_binding=None)
    async with session_factory() as session:
        with pytest.raises(ModelBindingError):
            await AgentInstanceManager(session).spawn(agent_id="a_1", session_id="s_1")


async def test_spawn_malformed_binding_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory, model_binding={"kind": "magic"})
    async with session_factory() as session:
        with pytest.raises(ModelBindingError):
            await AgentInstanceManager(session).spawn(agent_id="a_1", session_id="s_1")


async def test_spawn_duplicate_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        manager = AgentInstanceManager(session)
        await manager.spawn(agent_id="a_1", session_id="s_1")
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(InstanceExistsError):
            await AgentInstanceManager(session).spawn(agent_id="a_1", session_id="s_1")
        count = (
            await session.execute(select(AgentInstance))
        ).scalars().all()
        assert len(count) == 1
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/agent/test_instances.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'octave.agent.instances'`

- [ ] **Step 4: Add errors** — in `backend/src/octave/agent/errors.py`, extend `__all__` and add classes:

```python
__all__ = [
    "AgentError",
    "AgentNotFoundError",
    "AgentPausedError",
    "InstanceExistsError",
    "InstanceNotFoundError",
    "InvalidTransitionError",
    "ModelBindingError",
    "SessionNotFoundError",
    "TerminalSessionError",
    "ToolLoopLimitError",
    "TurnInProgressError",
]


class AgentError(Exception):
    """Base for lifecycle-gate failures (design spec 2026-09-27)."""


class AgentNotFoundError(AgentError):
    """No such agent definition."""


class SessionNotFoundError(AgentError):
    """No such session."""


class AgentPausedError(AgentError):
    """Definition-level gate: paused agents do not spawn or take turns."""


class TerminalSessionError(AgentError):
    """Session status is completed/failed/cancelled; no instances spawn."""


class InstanceExistsError(AgentError):
    """One live instance per agent per session (uq constraint)."""


class InstanceNotFoundError(AgentError):
    """No such instance."""


class TurnInProgressError(AgentError):
    """begin_turn lost the idle→active race; the instance is already active."""


class InvalidTransitionError(AgentError):
    """Operation requires a state the instance is not in."""


class ModelBindingError(AgentError):
    """Binding missing, malformed, or (for tag form) unresolvable."""
```

- [ ] **Step 5: Implement the manager** — create `backend/src/octave/agent/instances.py`

```python
"""Agent instance lifecycle (design spec 2026-09-27).

The only write path for ``agent_instances``. Follows the VaultStore
convention: constructed with the caller's AsyncSession, never commits.
Instances carry no context — the session transcript and the vault own it.
"""

import logging
import uuid
from collections.abc import Callable

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from octave.agent.errors import (
    AgentNotFoundError,
    AgentPausedError,
    InstanceExistsError,
    InstanceNotFoundError,
    InvalidTransitionError,
    ModelBindingError,
    SessionNotFoundError,
    TerminalSessionError,
    TurnInProgressError,
)
from octave.db.models import (
    Agent,
    AgentInstance,
    Participant,
    Session,
    SessionParticipant,
)
from octave.db.models.base import utcnow
from octave.db.types import AgentStatus, InstanceStatus, ModelBinding

__all__ = ["AgentInstanceManager", "ResolvedModel", "resolve_model"]

logger = logging.getLogger(__name__)

_TERMINAL_SESSION_STATUSES = frozenset({"completed", "failed", "cancelled"})

_BINDING_ADAPTER: TypeAdapter[ModelBinding] = TypeAdapter(ModelBinding)


def _validate_binding(agent: Agent) -> None:
    """Spawn-time gate: shape only. Tag resolution happens at turn start
    via ``resolve_model`` (see design spec §4 and plan Deviations)."""
    if agent.model_binding is None:
        raise ModelBindingError(f"agent {agent.id} has no model_binding")
    try:
        _BINDING_ADAPTER.validate_python(agent.model_binding)
    except ValidationError as exc:
        raise ModelBindingError(
            f"agent {agent.id} has invalid model_binding: {exc}"
        ) from exc


class AgentInstanceManager:
    """spawn / begin_turn / end_turn / destroy / reconcile. Never commits;
    callers own transaction boundaries (see ``octave.db.deps``)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def spawn(self, *, agent_id: str, session_id: str) -> AgentInstance:
        """Bind an active definition to a live session.

        Ensures the agent has a ``Participant`` row and active membership
        (re-invite resets ``left_at``). Raises the matching
        ``AgentError`` subclass on every gate.
        """
        agent = await self._session.get(Agent, agent_id)
        if agent is None:
            raise AgentNotFoundError(agent_id)
        if agent.status != AgentStatus.ACTIVE:
            raise AgentPausedError(agent_id)
        _validate_binding(agent)
        session_row = await self._session.get(Session, session_id)
        if session_row is None:
            raise SessionNotFoundError(session_id)
        if session_row.status in _TERMINAL_SESSION_STATUSES:
            raise TerminalSessionError(session_id, session_row.status)
        existing = await self._session.execute(
            select(AgentInstance).where(
                AgentInstance.agent_id == agent_id,
                AgentInstance.session_id == session_id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise InstanceExistsError(agent_id, session_id)
        participant = (
            await self._session.execute(
                select(Participant).where(Participant.agent_id == agent_id)
            )
        ).scalar_one_or_none()
        if participant is None:
            participant = Participant(
                id=uuid.uuid4().hex, agent_id=agent_id, label=agent.name
            )
            self._session.add(participant)
            await self._session.flush()
        membership = await self._session.get(
            SessionParticipant, (session_id, participant.id)
        )
        if membership is None:
            self._session.add(
                SessionParticipant(session_id=session_id, participant_id=participant.id)
            )
        elif membership.left_at is not None:
            membership.left_at = None  # re-invite: membership is live again
        instance = AgentInstance(
            id=uuid.uuid4().hex,
            agent_id=agent_id,
            session_id=session_id,
            status=str(InstanceStatus.IDLE),
        )
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def _get(self, instance_id: str) -> AgentInstance:
        instance = await self._session.get(AgentInstance, instance_id)
        if instance is None:
            raise InstanceNotFoundError(instance_id)
        return instance
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/agent/test_instances.py -v`
Expected: all PASS

- [ ] **Step 7: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run mypy src && uv run pytest -q
git add tests/conftest.py tests/db/conftest.py src/octave/agent/errors.py src/octave/agent/instances.py tests/agent/test_instances.py
git commit -m "feat(agent): AgentInstanceManager.spawn with lifecycle gates (#25)"
```

---

## Task 6: begin_turn / end_turn (turn mutex)

**Files:**
- Modify: `backend/src/octave/agent/instances.py`
- Test: `backend/tests/agent/test_instances.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/agent/test_instances.py`

```python
async def _spawn_idle(
    session_factory: async_sessionmaker[AsyncSession],
) -> str:
    """Seed defs, spawn, commit; return the instance id."""
    await _seed_defs(session_factory)
    async with session_factory() as session:
        instance = await AgentInstanceManager(session).spawn(
            agent_id="a_1", session_id="s_1"
        )
        await session.commit()
        return instance.id


async def test_begin_turn_activates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        instance = await AgentInstanceManager(session).begin_turn(instance_id)
        await session.commit()
        assert instance.status == "active"


async def test_begin_turn_twice_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        await AgentInstanceManager(session).begin_turn(instance_id)
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(TurnInProgressError):
            await AgentInstanceManager(session).begin_turn(instance_id)


async def test_begin_turn_paused_agent_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        agent = await session.get(Agent, "a_1")
        assert agent is not None
        agent.status = "paused"
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(AgentPausedError):
            await AgentInstanceManager(session).begin_turn(instance_id)


async def test_begin_turn_missing_instance_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        with pytest.raises(InstanceNotFoundError):
            await AgentInstanceManager(session).begin_turn("ghost")


async def test_end_turn_idles(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        manager = AgentInstanceManager(session)
        await manager.begin_turn(instance_id)
        instance = await manager.end_turn(instance_id)
        await session.commit()
        assert instance.status == "idle"


async def test_end_turn_on_idle_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        with pytest.raises(InvalidTransitionError):
            await AgentInstanceManager(session).end_turn(instance_id)
```

Add `TurnInProgressError` and `InvalidTransitionError` to the errors import at the top of the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agent/test_instances.py -v -k "turn"`
Expected: FAIL — `AttributeError: 'AgentInstanceManager' object has no attribute 'begin_turn'`

- [ ] **Step 3: Implement** — in `backend/src/octave/agent/instances.py`, add methods to `AgentInstanceManager` (after `spawn`):

```python
    async def begin_turn(self, instance_id: str) -> AgentInstance:
        """Atomically claim the idle→active transition (the turn mutex).

        Re-checks the definition gate: a pause after spawn blocks new
        turns; it never interrupts an active one (pause is a gate, not an
        interrupt). Rowcount 0 means someone else holds the turn.
        """
        instance = await self._get(instance_id)
        agent = await self._session.get(Agent, instance.agent_id)
        if agent is None:  # RESTRICT makes this unreachable; defensive
            raise AgentNotFoundError(instance.agent_id)
        if agent.status != AgentStatus.ACTIVE:
            raise AgentPausedError(instance.agent_id)
        result = await self._session.execute(
            update(AgentInstance)
            .where(
                AgentInstance.id == instance_id,
                AgentInstance.status == str(InstanceStatus.IDLE),
            )
            .values(status=str(InstanceStatus.ACTIVE), updated_at=utcnow())
        )
        if not result.rowcount:
            raise TurnInProgressError(instance_id)
        await self._session.refresh(instance)
        return instance

    async def end_turn(self, instance_id: str) -> AgentInstance:
        """Release the turn: active→idle. Turn failures use this too —
        the error is an event in the session, not instance state."""
        instance = await self._get(instance_id)
        if instance.status == str(InstanceStatus.IDLE):
            raise InvalidTransitionError(
                f"instance {instance_id}: end_turn requires active, got idle"
            )
        result = await self._session.execute(
            update(AgentInstance)
            .where(
                AgentInstance.id == instance_id,
                AgentInstance.status == str(InstanceStatus.ACTIVE),
            )
            .values(status=str(InstanceStatus.IDLE), updated_at=utcnow())
        )
        if not result.rowcount:
            raise InvalidTransitionError(
                f"instance {instance_id}: turn ended concurrently"
            )
        await self._session.refresh(instance)
        return instance
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agent/test_instances.py -v`
Expected: all PASS

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run mypy src && uv run pytest -q
git add src/octave/agent/instances.py tests/agent/test_instances.py
git commit -m "feat(agent): turn mutex via atomic idle/active transitions (#25)"
```

---

## Task 7: destroy + reconcile

**Files:**
- Modify: `backend/src/octave/agent/instances.py`
- Test: `backend/tests/agent/test_instances.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/agent/test_instances.py`

```python
async def test_destroy_deletes_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        destroyed = await AgentInstanceManager(session).destroy(instance_id)
        await session.commit()
        assert destroyed is True
    async with session_factory() as session:
        assert await session.get(AgentInstance, instance_id) is None


async def test_destroy_absent_returns_false(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await AgentInstanceManager(session).destroy("ghost") is False


async def test_destroy_active_is_plain_delete(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """No archival hook, no state precondition — destroy is a delete."""
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        manager = AgentInstanceManager(session)
        await manager.begin_turn(instance_id)
        assert await manager.destroy(instance_id) is True
        await session.commit()


async def test_reconcile_resets_stale_active(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        await AgentInstanceManager(session).begin_turn(instance_id)
        await session.commit()
    async with session_factory() as session:
        reset = await AgentInstanceManager(session).reconcile()
        await session.commit()
        assert reset == 1
    async with session_factory() as session:
        instance = await session.get(AgentInstance, instance_id)
        assert instance is not None and instance.status == "idle"


async def test_reconcile_noop_when_all_idle(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _spawn_idle(session_factory)
    async with session_factory() as session:
        assert await AgentInstanceManager(session).reconcile() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agent/test_instances.py -v -k "destroy or reconcile"`
Expected: FAIL — `AttributeError: ... has no attribute 'destroy'`

- [ ] **Step 3: Implement** — in `backend/src/octave/agent/instances.py`, add methods to `AgentInstanceManager` (after `end_turn`):

```python
    async def destroy(self, instance_id: str) -> bool:
        """Hard-delete the instance binding. False if absent.

        Pure delete by design: no archival hook, nothing to roll back.
        Archival rides the turn boundary (end_turn), not this call —
        design spec "Archival Contract".
        """
        result = await self._session.execute(
            delete(AgentInstance).where(AgentInstance.id == instance_id)
        )
        return bool(result.rowcount)

    async def reconcile(self) -> int:
        """Reset stale active rows to idle after an unclean shutdown.

        The interrupted turn is already visible as a truncated transcript
        in events; no zombie states survive restart. Returns rows reset.
        """
        result = await self._session.execute(
            update(AgentInstance)
            .where(AgentInstance.status == str(InstanceStatus.ACTIVE))
            .values(status=str(InstanceStatus.IDLE), updated_at=utcnow())
        )
        if result.rowcount:
            logger.warning(
                "reconciled %s stale active instance(s) to idle", result.rowcount
            )
        return int(result.rowcount)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agent/test_instances.py -v`
Expected: all PASS

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run mypy src && uv run pytest -q
git add src/octave/agent/instances.py tests/agent/test_instances.py
git commit -m "feat(agent): destroy is plain delete; reconcile resets stale active rows (#25)"
```

---

## Task 8: Model binding resolution

**Files:**
- Modify: `backend/src/octave/agent/instances.py`
- Test: `backend/tests/agent/test_instances.py`

- [ ] **Step 1: Write the failing tests** — append to `backend/tests/agent/test_instances.py`

Extend the imports at the top:

```python
from octave.agent.instances import ResolvedModel, resolve_model
from octave.db.types import ExplicitModelBinding, TagModelBinding
```

Then:

```python
def test_resolve_explicit_binding() -> None:
    resolved = resolve_model(
        ExplicitModelBinding(kind="explicit", adapter="openai", model="llama3")
    )
    assert resolved == ResolvedModel(adapter="openai", model="llama3")


def test_resolve_tag_binding_with_lookup() -> None:
    resolved = resolve_model(
        TagModelBinding(kind="tag", tag="quick"), tag_lookup=lambda tag: "llama3"
    )
    assert resolved == ResolvedModel(adapter=None, model="llama3")


def test_resolve_tag_miss_raises() -> None:
    with pytest.raises(ModelBindingError):
        resolve_model(TagModelBinding(kind="tag", tag="nope"), tag_lookup=lambda t: None)


def test_resolve_tag_without_lookup_raises() -> None:
    with pytest.raises(ModelBindingError):
        resolve_model(TagModelBinding(kind="tag", tag="quick"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/agent/test_instances.py -v -k resolve`
Expected: FAIL — `ImportError: cannot import name 'resolve_model'` (the module declares it in `__all__` but it does not exist yet)

- [ ] **Step 3: Implement** — in `backend/src/octave/agent/instances.py`, add after `_validate_binding`:

```python
@dataclass(frozen=True)
class ResolvedModel:
    """Concrete (adapter, model) pair for a turn. ``adapter=None`` means
    "use the configured default adapter" (tag bindings defer engine
    selection to the inference layer)."""

    adapter: str | None
    model: str


def resolve_model(
    binding: ModelBinding,
    *,
    tag_lookup: Callable[[str], str | None] | None = None,
) -> ResolvedModel:
    """Resolve a binding to a concrete pair at turn start.

    ``tag_lookup`` maps a capability tag to a model name; the turn runner
    wires it once model tagging lands (Inference roadmap #7). A tag miss
    fails loud — an agent with no usable model is unusable, and silent
    fallback would hide misconfiguration (design spec §4).
    """
    if isinstance(binding, ExplicitModelBinding):
        return ResolvedModel(adapter=binding.adapter, model=binding.model)
    if tag_lookup is None:
        raise ModelBindingError(
            f"tag binding {binding.tag!r} requires a tag_lookup"
        )
    model = tag_lookup(binding.tag)
    if model is None:
        raise ModelBindingError(f"model tag {binding.tag!r} resolves to nothing")
    return ResolvedModel(adapter=None, model=model)
```

Add `from dataclasses import dataclass` to imports and `ExplicitModelBinding`, `TagModelBinding` to the `octave.db.types` import line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/agent/test_instances.py -v`
Expected: all PASS

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check . && uv run mypy src && uv run pytest -q
git add src/octave/agent/instances.py tests/agent/test_instances.py
git commit -m "feat(agent): resolve_model with fail-loud tag lookup (#25)"
```

---

## Task 9: Public surface + full verification

**Files:**
- Modify: `backend/src/octave/agent/__init__.py`
- Test: `backend/tests/agent/test_package.py`

- [ ] **Step 1: Write the failing test** — in `backend/tests/agent/test_package.py`, replace `test_public_names_are_exported` with:

```python
def test_public_names_are_exported() -> None:
    for name in (
        "AgentError",
        "AgentInstanceManager",
        "AgentNotFoundError",
        "AgentPausedError",
        "InstanceExistsError",
        "InstanceNotFoundError",
        "InvalidTransitionError",
        "McpToolExecutor",
        "ModelBindingError",
        "ResolvedModel",
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

Run: `uv run pytest tests/agent/test_package.py -v`
Expected: FAIL — `assert hasattr(octave.agent, 'AgentError')` → `AssertionError: AgentError`

- [ ] **Step 3: Implement** — replace `backend/src/octave/agent/__init__.py` with:

```python
"""Agent plane: tool-use orchestration (issue #79) and instance lifecycle
(design spec #25).

Composition layer — the only package importing both octave.mcp and
octave.inference, plus octave.db for the instance manager. Future Agent
Manager components (registry, router) land here as additional modules;
they do not exist yet.
"""

from octave.agent.errors import (
    AgentError,
    AgentNotFoundError,
    AgentPausedError,
    InstanceExistsError,
    InstanceNotFoundError,
    InvalidTransitionError,
    ModelBindingError,
    SessionNotFoundError,
    TerminalSessionError,
    ToolLoopLimitError,
    TurnInProgressError,
)
from octave.agent.executor import ToolExecutor
from octave.agent.instances import AgentInstanceManager, ResolvedModel, resolve_model
from octave.agent.loop import ToolLoop
from octave.agent.mcp_executor import McpToolExecutor
from octave.agent.types import ToolOutcome, ToolTurn
from octave.tools.errors import ToolError

__all__ = [
    "AgentError",
    "AgentInstanceManager",
    "AgentNotFoundError",
    "AgentPausedError",
    "InstanceExistsError",
    "InstanceNotFoundError",
    "InvalidTransitionError",
    "McpToolExecutor",
    "ModelBindingError",
    "ResolvedModel",
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
]
```

- [ ] **Step 4: Run the full suite, lint, typecheck**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src`
Expected: `-- OK --` on all three; `test_package.py::test_no_sdk_imports` still passes (`octave.db` imports are not SDK imports).

- [ ] **Step 5: Commit**

```bash
git add src/octave/agent/__init__.py tests/agent/test_package.py
git commit -m "feat(agent): export instance lifecycle public surface (#25)"
```

---

## Spec coverage self-check

| Spec requirement | Task |
|---|---|
| `AgentStatus` / `InstanceStatus` / `ModelBinding` / `AgentAssignments` in `octave.db.types` | 1 |
| `agents`: `model_binding` replaces `model_tag`; `assignments` JSON; status narrowed | 2 |
| `agent_instances` table: unique binding, RESTRICT/CASCADE FKs, idle default | 3 |
| `model_tag` data survives as tag-form binding | 4 |
| Spawn gates (paused, terminal session, duplicate, missing, binding shape) + participant/membership ensure + re-invite | 5 |
| Turn mutex (atomic idle→active; end_turn; pause gate on begin_turn; no failed state) | 6 |
| Destroy = plain delete (archival decoupled); crash reconcile | 7 |
| Binding resolution, fail-loud tag miss, not persisted | 8 |
| Public surface; SDK quarantine intact | 9 |

Explicitly **not in this plan** (spec non-goals): router, result collector, inter-agent sharing, scheduling, REST/UI, archival implementation, link tables, `events.instance_id`, definition deletion. Follow-up issues to file after merge (spec "Follow-up Issues"): definition deletion semantics; save-time adapter validation for `ExplicitModelBinding`.
