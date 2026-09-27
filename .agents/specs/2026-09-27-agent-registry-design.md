# Agent Registry — Design

> Issue: #26 · PR: #106 · Branch: `feature/agent-registry`
> Date: 2026-09-27 · Status: Approved in brainstorming
> Depends on: 2026-09-27 Agent Lifecycle Model design (#25, PR #105 — shipped)
> Consumers: Agent Manager roadmap #3–#6, Agent Manager View UI

## Problem Statement

Issue #26 asks for the agent registry: a central source of truth tracking all
running agents, their IDs, current lifecycle state, and assigned context,
persisted to the database.

The #25 lifecycle design already shipped the persistence substrate:
`agent_instances` rows *are* the durable record of which agents are running —
spawn inserts, destroy hard-deletes, session end cascades, and crash
reconciliation resets stale `active` rows. Its Consequences section decides
this work item's shape in advance: *"Agent Manager #2 (registry) becomes
queries over `agent_instances` rather than a parallel bookkeeping structure."*

This work item therefore adds the **read surface** over that substrate —
typed, filterable, join-enriched queries — without introducing a second
source of truth.

## Design Decisions (from brainstorming)

| # | Decision | Rejected alternative |
|---|----------|----------------------|
| 1 | Read-only `AgentRegistry` class in `octave.agent.registry`, separate from the write-path `AgentInstanceManager`; constructed with a caller-supplied `AsyncSession`, never commits | Query methods bolted onto `AgentInstanceManager`; a parallel `agent_registry` table |
| 2 | **No new table, no migration** — persistence shipped with #25; the registry is SELECTs over `agent_instances` ⋈ `agents` | A mirrored registry table (dual-write desync; contradicts the approved #25 design) |
| 3 | Assigned context is **raw pass-through**: `agents.assignments` JSON references + agent name; no vault resolution | Joining `vault_items` to hydrate prompt/skill content (couples the registry to Context Manager #4 injection semantics); an opt-in `resolve=True` flag (second code path, no confirmed consumer) |
| 4 | Lean surface: `list_instances` (composable filters), `get_instance` (raise on miss), `count_by_status` | List-only minimalism (every consumer re-implements get/count); speculative session-centric views (`instances_for_session`, `sessions_for_agent`) |
| 5 | **Library-level only** — no REST/WebSocket routes | A read-only REST surface (route-layer conventions are not yet established by any agent work item; composition arrives with Integration & Testing #1, per #25) |

## Read Model

`RunningAgent` — frozen dataclass, one row of the registry view:

| Field | Type | Source |
|---|---|---|
| `instance_id` | `str` | `agent_instances.id` |
| `agent_id` | `str` | `agent_instances.agent_id` |
| `agent_name` | `str` | `agents.name` |
| `definition_status` | `AgentStatus` | `agents.status`, parsed |
| `instance_status` | `InstanceStatus` | `agent_instances.status`, parsed |
| `session_id` | `str` | `agent_instances.session_id` |
| `assignments` | `AgentAssignments` | `agents.assignments` JSON, validated leniently (below) |
| `created_at` / `updated_at` | `datetime` | `agent_instances` timestamps |

`model_binding` is deliberately **not** in the read model: the issue names
IDs, lifecycle state, and assigned context. If the dashboard needs the model
binding, adding one field is additive.

## Interface

```python
class AgentRegistry:
    """Read-only queries over agent_instances ⋈ agents. Never commits;
    never mutates. Constructed with the caller's AsyncSession
    (VaultStore / AgentInstanceManager convention)."""

    def __init__(self, session: AsyncSession) -> None: ...

    async def list_instances(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
        status: InstanceStatus | None = None,
    ) -> list[RunningAgent]: ...

    async def get_instance(self, instance_id: str) -> RunningAgent: ...

    async def count_by_status(self) -> dict[InstanceStatus, int]: ...
```

### Query semantics

- **Join**: inner join `agent_instances` → `agents`. The `RESTRICT` FK
  guarantees every instance has its definition row; no orphan handling.
  `sessions` is *not* joined — `session_id` lives on the instance row and no
  session column is read.
- **Filters** compose with AND; `status` filters the *instance* status
  (`idle | active`), not the definition status.
- **Ordering**: `created_at ASC, id ASC` (deterministic; `id` breaks
  same-timestamp ties).
- **`get_instance`** raises `InstanceNotFoundError` (existing class in
  `octave.agent.errors`) on a miss — same contract as the manager's `_get`.
- **`count_by_status`** returns both enum keys, zero-filled, over all rows
  (no filters — it is a dashboard counter, not a filtered aggregate).
- **No pagination**: local-first single-user workload; deferred the same way
  `ToolRegistry` deferred cursor pagination (follow-up if a consumer needs it).

### Lenient parsing (reporting surface, not a validation gate)

The registry must never raise on data it merely reports:

- `assignments` JSON invalid → log a warning, substitute
  `AgentAssignments()` (empty). Mirrors the #25 dangling-reference rule
  (skip + warn): malformed references are a write-path bug, but a registry
  that 500s because one definition row is corrupt would hide it worse.
- `agents.status` / `agent_instances.status` outside the enum → raise.
  These are written only by the manager and definition CRUD (which
  validates); an out-of-vocabulary value means genuine DB corruption, and
  silently coercing it would misreport lifecycle state. The asymmetry is
  deliberate: assignments are *references* (tolerate), status *is* the
  registry's subject matter (fail loud).

## Lifecycle Integration

The registry adds no states and enforces no transitions — #25's state
machine and turn mutex remain the only write path. Notable reported states:

- **Paused definition + live instance** is legitimate (pause is a gate, not
  an interrupt; active turns run to completion). The registry reports both
  statuses side by side; consumers decide policy.
- **Crash reconciliation** stays owned by the write path
  (`AgentInstanceManager.reconcile` on startup). The registry reports current
  rows as-is; it never invokes the manager.
- **Destroyed instances** simply disappear from every query (hard delete).

## Error Handling

- `get_instance` miss → `InstanceNotFoundError`. No new error classes.
- `assignments` malformed → warn + empty model (above).
- Registry methods issue SELECTs only; nothing to roll back, nothing to
  commit. Callers own transaction boundaries (`octave.db.deps`).

## Non-Goals

- REST/WebSocket routes, frontend wiring (composition = Integration #1; UI =
  Agent Manager View).
- Vault resolution of assignment references (Context Manager #4).
- Message routing (#3), result collection (#4), inter-agent sharing (#5),
  priority/scheduling (#6).
- New tables, migrations, or indexes (`agent_instances` indexes from #25
  suffice for the filter columns).
- Pagination, sorting options, projection options.

## Testing

Real SQLite via the shared `session_factory` fixture; seed helpers modeled
on `tests/agent/test_instances.py` (`_seed_defs`):

- **list_instances**: empty DB → `[]`; multiple instances across
  agents/sessions → all returned with joined fields correct (name,
  definition_status, assignments pass-through); each filter alone
  (`agent_id`, `session_id`, `status`) and combined; ordering deterministic.
- **get_instance**: returns the enriched record; missing id raises
  `InstanceNotFoundError`.
- **count_by_status**: zero-filled on empty DB; mixed idle/active counts
  both keys; destroyed instances excluded.
- **Lifecycle visibility**: spawn → appears; `begin_turn` → status flips to
  active; `end_turn` → idle; `destroy` → gone from list/get/count.
- **Lenient assignments**: corrupt `agents.assignments` JSON → warning logged
  (caplog), `AgentAssignments()` returned, no raise.
- **No mutation**: registry calls leave the DB byte-identical and require no
  commit (fresh session, no writes flushed).
- **Package exports**: `AgentRegistry`, `RunningAgent` in `octave.agent`
  `__all__` (mirrors `test_package.py` convention).

## Implementation Sketch

Paths for the implementation plan to refine:

| File | Change |
|---|---|
| `backend/src/octave/agent/registry.py` | new: `RunningAgent` dataclass, `AgentRegistry` (list/get/count) |
| `backend/src/octave/agent/__init__.py` | export `AgentRegistry`, `RunningAgent` |
| `backend/tests/agent/test_registry.py` | new: all tests above |
| `docs/TODO.md` | mark Agent Manager #2 done |

## Consequences

- `octave.agent` gains its first read surface; consumers (#3 router, #4
  collector, dashboard UI) depend on `RunningAgent` as their DTO contract.
- The SDK-quarantine AST guard is unaffected (no `openai`/`mcp` imports).
- `octave.agent.registry` is the third `registry` module in the codebase
  (`octave.mcp.registry` = tool inventory, `octave.db.registry` = adapter
  registry). Module paths disambiguate; the name matches the roadmap
  vocabulary ("agent registry").
- No schema change means #26 ships with a green migration chain untouched —
  the registry is purely additive Python.
