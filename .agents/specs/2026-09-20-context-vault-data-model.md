# Context Vault Data Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `VaultKind` enum as app-level validation for `vault_items.kind` and record the context-vault data model conventions (thin envelope, five kinds, two-tier run archival) in the design doc, diagrams, and ADRs.

**Architecture:** Design-only reconciliation of the shipped `vault_items` schema (PR #84) — the single code deliverable is a `StrEnum` in `octave.db.types` mirroring the existing `EventKind` pattern (TEXT column, app-level validation, no DB CHECK). Everything else is documentation: the design doc itself (written in architect mode, committed here), a docstring fix, the `data-flow.md` ER diagram, and two ADR entries. No migration, no new tables, no Pydantic content models, no CRUD.

**Tech Stack:** Python 3.13 / SQLAlchemy 2.0 / pytest / uv / ruff / mypy. All commands run from `backend/`.

**Spec:** [`.agents/specs/2026-09-20-context-vault-data-model-design.md`](./2026-09-20-context-vault-data-model-design.md)
**Branch:** `feature/context-vault-data-model` · **Draft PR:** [#96](https://github.com/Svagtlys/Octave/pull/96) · **Issue:** #31

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `.agents/specs/2026-09-20-context-vault-data-model-design.md` | commit (already written) | The approved design doc |
| `backend/src/octave/db/types.py` | modify | Add `VaultKind` StrEnum + `__all__` entry + module-docstring mention |
| `backend/src/octave/db/__init__.py` | modify | Re-export `VaultKind` (public vocabulary) |
| `backend/tests/db/test_types.py` | modify | `VaultKind` tests mirroring `EventKind` tests |
| `backend/src/octave/db/models/vault.py` | modify | `VaultItem` docstring → five-kind vocabulary |
| `docs/diagrams/data-flow.md` | modify | ER `kind` comment + conventions note under "Planned" section |
| `.agents/memory/decisions.md` | modify | New ADR + amendment note on the 2026-06-27 KB-separation ADR |

Historical specs (`.agents/specs/2026-09-13-*`, the 2026-06-27 ADR body itself) are **not** edited — they are records; the amendment note is appended, not a rewrite.

---

### Task 1: Commit the design document

The design doc was written in architect mode (which cannot run git). Commit it first so the PR carries the approved design before any code.

**Files:**
- Commit: `.agents/specs/2026-09-20-context-vault-data-model-design.md`

- [ ] **Step 1: Verify branch and file presence**

Run: `git branch --show-current && ls .agents/specs/2026-09-20-context-vault-data-model-design.md`
Expected: `feature/context-vault-data-model` and the file path echoed. If the branch is wrong, `git checkout feature/context-vault-data-model` first.

- [ ] **Step 2: Commit**

```bash
git add .agents/specs/2026-09-20-context-vault-data-model-design.md
git commit -m "docs(context): add context vault data model design for #31"
```

- [ ] **Step 3: Push**

```bash
git push -u origin feature/context-vault-data-model
```

---

### Task 2: `VaultKind` enum (TDD)

**Files:**
- Modify: `backend/tests/db/test_types.py` (add tests + import)
- Modify: `backend/src/octave/db/types.py` (enum after `EventKind`, `__all__`, module docstring)
- Modify: `backend/src/octave/db/__init__.py` (re-export)
- Test: `backend/tests/db/test_types.py`

- [ ] **Step 1: Write the failing tests**

In `backend/tests/db/test_types.py`, extend the import block (lines 6–11) to include `VaultKind`:

```python
from octave.db.types import (
    AssistantMessagePayload,
    EventKind,
    UserMessagePayload,
    VaultKind,
    VectorHit,
)
```

Append these tests at the end of the file (after `test_assistant_payload_model_is_optional`):

```python
def test_vault_kind_wire_values() -> None:
    assert VaultKind.SKILL == "skill"
    assert VaultKind.PROMPT == "prompt"
    assert VaultKind.PREFERENCE == "preference"
    assert VaultKind.RUN_SUMMARY == "run_summary"
    assert VaultKind.RUN_RECORD == "run_record"


def test_vault_kind_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        VaultKind("agent_state")


def test_vault_kind_revalidates_stored_text() -> None:
    """``vault_items.kind`` is TEXT in the DB; the enum is the app-level guard."""
    assert VaultKind(VaultKind.RUN_RECORD.value) is VaultKind.RUN_RECORD


def test_vault_kind_membership_is_exhaustive() -> None:
    assert {kind.value for kind in VaultKind} == {
        "skill",
        "prompt",
        "preference",
        "run_summary",
        "run_record",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/db/test_types.py -v`
Expected: collection ERROR — `ImportError: cannot import name 'VaultKind' from 'octave.db.types'`.

- [ ] **Step 3: Write minimal implementation**

In `backend/src/octave/db/types.py`:

3a. Update the module docstring's first sentence (lines 3–4) so the enum's role covers the vault column:

```python
"""Octave DB domain types.

``events.kind``, ``sessions.status``, and ``vault_items.kind`` are TEXT
columns: the enums here are
the app-level validation layer. A DB ``CHECK`` would force an ``ALTER TABLE``
```

(Only the first sentence changes; the rest of the docstring is untouched.)

3b. Add the enum immediately after the `EventKind` class (after line 33, before `@dataclass(frozen=True)`):

```python
class VaultKind(StrEnum):
    """One vault item's type. Values are stored verbatim in ``vault_items.kind``.

    App-level validation layer (see module docstring): the enum grows freely —
    a new kind costs one member, no migration. Conventions per kind live in
    the context vault data model design spec.
    """

    SKILL = "skill"
    PROMPT = "prompt"
    PREFERENCE = "preference"
    RUN_SUMMARY = "run_summary"
    RUN_RECORD = "run_record"
```

3c. Add `"VaultKind"` to `__all__` (alphabetical, between `"UserMessagePayload"` and `"VectorHit"`):

```python
__all__ = [
    "AssistantMessagePayload",
    "EventKind",
    "UserMessagePayload",
    "VaultKind",
    "VectorHit",
]
```

- [ ] **Step 4: Re-export from the package**

In `backend/src/octave/db/__init__.py`, update line 22 and `__all__`:

```python
from octave.db.types import EventKind, VaultKind, VectorHit
```

and in `__all__`, insert `"VaultKind",` immediately before `"VectorHit",` (alphabetical: `VaultKind` < `VectorHit`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/db/test_types.py -v`
Expected: all tests PASS, including the 4 new `test_vault_kind_*` tests.

- [ ] **Step 6: Full gates**

Run: `cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: pytest all-pass, ruff `All checks passed!`, mypy `Success: no issues found`.

- [ ] **Step 7: Commit**

```bash
git add backend/src/octave/db/types.py backend/src/octave/db/__init__.py backend/tests/db/test_types.py
git commit -m "feat(db): add VaultKind enum validating vault_items.kind"
```

---

### Task 3: `VaultItem` docstring — five-kind vocabulary

The model docstring still names `agent_state`, a kind that never shipped. Pure documentation; no behavior change, no test.

**Files:**
- Modify: `backend/src/octave/db/models/vault.py:29`

- [ ] **Step 1: Update the class docstring**

In `backend/src/octave/db/models/vault.py`, replace:

```python
class VaultItem(Base):
    """One vault entry: skill | prompt | preference | agent_state."""
```

with:

```python
class VaultItem(Base):
    """One vault entry — see ``VaultKind``: skill | prompt | preference |
    run_summary | run_record."""
```

- [ ] **Step 2: Verify no stray `agent_state` remains in live code**

Run: `grep -rn "agent_state" backend/src backend/tests`
Expected: no output. (Historical specs and the 2026-06-27/09-13 design docs intentionally keep the old name as a record.)

- [ ] **Step 3: Gates**

Run: `cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all green (docstring-only change).

- [ ] **Step 4: Commit**

```bash
git add backend/src/octave/db/models/vault.py
git commit -m "docs(db): VaultItem docstring to shipped VaultKind vocabulary"
```

---

### Task 4: `data-flow.md` — ER diagram kind + conventions note

**Files:**
- Modify: `docs/diagrams/data-flow.md:63` (ER `kind` comment)
- Modify: `docs/diagrams/data-flow.md:98-99` ("Planned, not yet created" section)

- [ ] **Step 1: Update the ER comment**

In `docs/diagrams/data-flow.md`, replace:

```
    string kind "skill | prompt | preference | agent_state"
```

with:

```
    string kind "skill | prompt | preference | run_summary | run_record"
```

- [ ] **Step 2: Extend the planned-section footer**

Replace:

```
Planned, not yet created (additive migrations in their consumer work items):
`SKILL_LINK`, `TOOL_TAG`, `MODEL_TAG`, `TOOLS`, `INJECTION_RULES`.
```

with:

```
Planned, not yet created (additive migrations in their consumer work items):
`SKILL_LINK`, `TOOL_TAG`, `MODEL_TAG`, `TOOLS`, `INJECTION_RULES`.

Vault item structure conventions (`metadata` tags, links, skill params, run
provenance) are documented in the
[context vault data model design](../../.agents/specs/2026-09-20-context-vault-data-model-design.md).
Run archival is two-tier: `run_summary` (Tier 1 — "which run?") then
`run_record` (Tier 2 — verbatim chunks), linked by `metadata.session_id`.
```

- [ ] **Step 3: Sanity-check the relative link resolves**

Run: `test -f "$(dirname docs/diagrams/data-flow.md)/../../.agents/specs/2026-09-20-context-vault-data-model-design.md" && echo OK`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add docs/diagrams/data-flow.md
git commit -m "docs(context): vault kind vocabulary and two-tier archival in data-flow"
```

---

### Task 5: ADR — vault data model + KB-separation amendment

**Files:**
- Modify: `.agents/memory/decisions.md` (amend 2026-06-27 ADR; append new ADR at end)

- [ ] **Step 1: Append the amendment to the 2026-06-27 ADR**

In `.agents/memory/decisions.md`, locate the "2026-06-27 — Context Vault Separation from Knowledge Base" section. After its final consequence line (`- Context Vault is read-only (edits go to KB source)`), insert:

```markdown

**Amendment (2026-09-20):** Superseded on one point — the shipped schema treats `vault_items.content` as canonical for injectable context; the vault is not a purely derived, read-only index. Externally-owned (KB/MCP-populated) items, if they arrive with the Context Manager's vault-builder work item, will carry their own provenance semantics. See the 2026-09-20 Context Vault Data Model ADR below.
```

- [ ] **Step 2: Append the new ADR at the end of the Decisions section**

```markdown

### 2026-09-20 — Context Vault Data Model: Thin Envelope, Five Kinds, Two-Tier Run Archival

**Context:** Issue #31 asks for vault item schemas (skills, prompts, preferences, agent state) plus relationships and foreign keys. The `vault_items` table already shipped (PR #84) with `content` as source of truth and a `metadata` JSON column; the 2026-09-13 design deferred link tables to consumer work items. The `agent_state` name was rejected in review: the kind is an archived, searchable record of a completed agent run, not live state.

**Options Considered:**
1. Structured JSON in `content` for machine kinds — breaks the invariant that `content` is what gets embedded and injected
2. Markdown + frontmatter in `content` — echoes this ADR's 2026-06-27 KB-files convention but conflicts with the shipped `metadata` column and pollutes embeddings unless stripped
3. Thin envelope — `content` is 100% injectable prose; all structure lives in `metadata` as documented conventions

**Decision:** Option 3. `VaultKind` (in `octave.db.types`) ships as the app-level validator for `vault_items.kind`: `skill | prompt | preference | run_summary | run_record` — `agent_state` renamed before any enum existed in code. Relationships are named references in `metadata` (`tags`, `links`, skill `params`, run provenance `session_id`/`agent_id`/`seq_range`); link tables (`skill_links`, `vault_tags`, `model_tags`, `injection_rules`) stay deferred to their consumer work items with trigger conditions recorded. Run archival is two-tier: one `run_summary` item per run (headline prose; Tier-1 "which run?" search) plus N `run_record` items (verbatim transcript chunks; Tier-2 search scoped by `metadata.session_id`).

**Rationale:** `content` is simultaneously the embedded text, the injected prompt text, and the re-embedding source of truth — non-prose structure inside it degrades embeddings and injection alike. Summaries alone lose crucial steps; one vector over a whole run retrieves badly. Tiering keeps the Tier-1 corpus small and precise while Tier 2 surfaces verbatim text. `kind` is an indexed column (`ix_vault_items_user_kind`), so tier filtering is cheap; `metadata` is a JSON blob — hence a fifth kind rather than a `meta.role` flag.

**Consequences:** The vault is canonical for injectable context (amends the 2026-06-27 decision above). Per-kind Pydantic validation models, chunking policy, archival triggers/dedup, and scoped vector search in `DbAdapter.search_similar` are explicitly deferred to the Context Manager storage/lifecycle work items — this decision records the requirements they must serve. Extra `metadata` keys are allowed, so future consumers extend conventions without coordination; the storage layer validates with `extra="allow"`.
```

- [ ] **Step 3: Verify ADR format consistency**

Confirm the new ADR uses the same bold-field structure (`Context / Options Considered / Decision / Rationale / Consequences`) as every other entry in the file.

- [ ] **Step 4: Commit**

```bash
git add .agents/memory/decisions.md
git commit -m "docs(memory): ADR for context vault data model; amend KB-separation ADR"
```

---

### Task 6: Final verification & PR

- [ ] **Step 1: Full gates from a clean state**

Run: `cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all green.

- [ ] **Step 2: Confirm the PR diff matches the deliverables list**

Run: `git log --oneline main..HEAD` (or the branch's base) — expected commits: design doc, `VaultKind` enum, docstring, data-flow, ADR. No migration files, no model changes beyond the docstring.

- [ ] **Step 3: Push and mark the PR ready for review**

```bash
git push
```

Update PR #96's body to link the spec: `.agents/specs/2026-09-20-context-vault-data-model-design.md`.
