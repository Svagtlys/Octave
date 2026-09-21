# Architectural Decision Records

## Format

Each decision follows this structure:

```markdown
### [Date] — Decision Title

**Context:** What situation led to this decision?
**Options Considered:** What alternatives were evaluated?
**Decision:** What was chosen?
**Rationale:** Why was this chosen?
**Consequences:** What are the downstream effects?
```

---

## Decisions

### 2026-06-27 — MCP as Universal Integration Abstraction

**Context:** Octave needs to support multiple integrations (filesystem, vector DB, automation engine) without hard-coding any specific implementation.

**Options Considered:**
1. Custom plugin system with Octave-specific SDK
2. MCP (Model Context Protocol) as the universal interface
3. Hybrid approach with native support for core components

**Decision:** Use MCP exclusively as the integration abstraction.

**Rationale:**
- MCP is becoming the standard for AI tool integration
- No vendor lock-in to any specific tool or service
- Pluggable and swappable via configuration alone
- Reduces Octave's codebase to pure orchestration logic

**Consequences:**
- All components must have MCP server implementations
- Depends on MCP ecosystem maturity
- Cannot optimize internal paths that cross MCP boundaries

### 2026-06-27 — Local-First Data Architecture

**Context:** User privacy and data ownership are core requirements.

**Options Considered:**
1. Cloud-hosted with local caching
2. Fully local with optional cloud sync
3. Strictly local-first

**Decision:** Strictly local-first. All data stays on-prem.

**Rationale:**
- User owns their knowledge base completely
- No external API calls required for core functionality
- Works offline
- Minimizes attack surface

**Consequences:**
- No built-in multi-user sync
- Backup responsibility falls on the user
- Must run local infrastructure (LLM, vector DB, etc.)

### 2026-06-27 — Context Vault Separation from Knowledge Base

**Context:** The KB contains all user knowledge, but not all of it should be injected into LLM context.

**Options Considered:**
1. Flat KB with everything in context
2. Separate context directory outside KB
3. Tagged content within KB, indexed into Context Vault

**Decision:** Tagged content within KB, indexed into Context Vault.

**Rationale:**
- User manages one KB directory
- Clear distinction between injectable content (preferences, skills, prompts) and searchable content (general knowledge)
- Context Vault is a derived index, not a separate data store

**Consequences:**
- Requires tagging convention (directory names, database metadata, or frontmatter)
- Rebuild process needed when KB changes
- Context Vault is read-only (edits go to KB source)

**Amendment (2026-09-20):** Superseded on one point — the shipped schema treats `vault_items.content` as canonical for injectable context; the vault is not a purely derived, read-only index. Externally-owned (KB/MCP-populated) items, if they arrive with the Context Manager's vault-builder work item, will carry their own provenance semantics. See the 2026-09-20 Context Vault Data Model ADR below.

### 2026-09-13 — Adapter-Style Database Engine Seam

**Context:** The architecture names both SQLite+vec0 and PostgreSQL+pgvector. We need one supported engine now without locking the schema to it, and the inference package already has a proven adapter pattern.

**Options Considered:**
1. SQLite only, hard-coded — simplest, abandons the stated dual-engine plan
2. Both engines now, with a dialect abstraction and dual CI — doubles migration and test surface before either is proven
3. Adapter seam (ABC + registry) mirroring `octave.inference`, one adapter registered — matches an established project pattern

**Decision:** Option 3. `DbAdapter` owns engine/session lifecycle, vector-store DDL, and similarity search; ORM models and the Alembic chain stay adapter-neutral; `sqlite_vec` is quarantined to `sqlite_adapter.py`/`_bootstrap.py`.

**Rationale:** The inference adapter is "thick per-verb, narrow in surface" — four complete operations, nothing else. Replicating that keeps the two pluggable subsystems recognisably identical. Per-table CRUD is NOT wrapped in repositories: SQLAlchemy already abstracts it, and wrapping it would re-abstract an abstraction.

**Consequences:** A pgvector adapter is additive (register a class, no schema change). Vector-index DDL cannot live in Alembic (it is engine-specific), so the index is adapter-managed and un-migrated — schema equivalence is enforced by a migration-vs-model test instead.

### 2026-09-13 — Sessions/Events/Participants Transcript Vocabulary

**Context:** The issue named `conversations` and `messages`. But not every LLM run is a conversation (automations, agent runs), not every transcript entry is a message (tool calls, results, context injection), and both users and agents must be able to author entries — including future A2A between agents.

**Options Considered:**
1. `conversations` + `messages` as specified — cheapest, forces a rename migration the first time an automation stores a transcript
2. `sessions` + `messages` — fixes the container; `messages` is defensible via LLM-API content-block semantics but invents a non-API kind for context injection
3. `sessions` + `events` + a `participants` identity supertype over `users`/`agents`

**Decision:** Option 3. `events.kind` is TEXT with app-level enum validation. `sessions.created_by_user_id` is the single owner; participation is N via `session_participants`.

**Rationale:** A `CHECK` on `kind` would force a table rebuild per new kind on SQLite. Ownership and participation are different axes — conflating them breaks the moment a session has two humans.

**Consequences:** Multi-party/A2A/autonomous sessions are *reachable* without re-modeling, but no orchestration behavior ships: no `mode`, `driver_participant_id`, or `turn_policy` column (deferred, with rationale, to the multi-agent work item). Multi-human sessions raise an unresolved vault-visibility question, tracked as a follow-up issue.

### 2026-09-20 — Context Vault Data Model: Thin Envelope, Five Kinds, Two-Tier Run Archival

**Context:** Issue #31 asks for vault item schemas (skills, prompts, preferences, agent state) plus relationships and foreign keys. The `vault_items` table already shipped (PR #84) with `content` as source of truth and a `metadata` JSON column; the 2026-09-13 design deferred link tables to consumer work items. The `agent_state` name was rejected in review: the kind is an archived, searchable record of a completed agent run, not live state.

**Options Considered:**
1. Structured JSON in `content` for machine kinds — breaks the invariant that `content` is what gets embedded and injected
2. Markdown + frontmatter in `content` — echoes this ADR's 2026-06-27 KB-files convention but conflicts with the shipped `metadata` column and pollutes embeddings unless stripped
3. Thin envelope — `content` is 100% injectable prose; all structure lives in `metadata` as documented conventions

**Decision:** Option 3. `VaultKind` (in `octave.db.types`) ships as the app-level validator for `vault_items.kind`: `skill | prompt | preference | run_summary | run_record` — `agent_state` renamed before any enum existed in code. Relationships are named references in `metadata` (`tags`, `links`, skill `params`, run provenance `session_id`/`agent_id`/`seq_range`); link tables (`skill_links`, `vault_tags`, `model_tags`, `injection_rules`) stay deferred to their consumer work items with trigger conditions recorded. Run archival is two-tier: one `run_summary` item per run (headline prose; Tier-1 "which run?" search) plus N `run_record` items (verbatim transcript chunks; Tier-2 search scoped by `metadata.session_id`).

**Rationale:** `content` is simultaneously the embedded text, the injected prompt text, and the re-embedding source of truth — non-prose structure inside it degrades embeddings and injection alike. Summaries alone lose crucial steps; one vector over a whole run retrieves badly. Tiering keeps the Tier-1 corpus small and precise while Tier 2 surfaces verbatim text. `kind` is an indexed column (`ix_vault_items_user_kind`), so tier filtering is cheap; `metadata` is a JSON blob — hence a fifth kind rather than a `meta.role` flag.

**Consequences:** The vault is canonical for injectable context (amends the 2026-06-27 decision above). Per-kind Pydantic validation models, chunking policy, archival triggers/dedup, and scoped vector search in `DbAdapter.search_similar` are explicitly deferred to the Context Manager storage/lifecycle work items — this decision records the requirements they must serve. Extra `metadata` keys are allowed, so future consumers extend conventions without coordination; the storage layer validates with `extra="allow"`.

### 2026-09-21 — Vector Mirror Behind the Adapter Seam; Aux-Column Filters; Caller-Supplied Embeddings

**Context:** Issue #32 ships the vault storage layer. vec0 is a virtual table that owns its data — unlike pgvector there is no index-on-a-column, so ANN search requires a second store (`vec_vault_items_<N>`) written through the `sqlite_vec` serializer, which is quarantined to `sqlite_adapter.py`. The 2026-09-20 data model requires Tier-2 search scoped by kind/session_id; vec0 applies filters pre-`k` only via auxiliary columns, and vec0 cannot `ALTER ADD COLUMN` — a one-way door that must be opened while the table is still disposable.

**Options Considered:** 1) consumers pair ORM writes with raw vec SQL (distributes the dual-write invariant across callers; breaks the quarantine); 2) repository emits vec SQL directly (breaks the quarantine); 3) `store_vector`/`remove_vector` on `DbAdapter` with no-op defaults, pairing owned by one `VaultStore`, aux columns added to the DDL now, embeddings caller-supplied.

**Decision:** Option 3. `VaultStore` (in `octave.db.vault_store`) owns the transaction pairing, the staleness rule (new content without a new vector NULLs the cache and drops the vec row), and `VaultKind`/`meta` validation; it never commits. `search_similar` gains keyword-only `kind`/`user_id`/`session_id` filters applied pre-`k` via aux columns (`session_id` denormalized from `meta.session_id`). Embeddings are caller-supplied — `octave.db` never imports `octave.inference`; the Context Manager service layer composes `embed → upsert` and owns the model-selection policy.

**Rationale:** The mirror is a SQLite-ism (pgvector's column *is* the index host), so the adapter methods default to no-ops and the conformance suite pins only the observable contract: store→findable, remove→gone, filters visible. The DDL change is free now and a full re-embed later; doing it later would strand filtered search in #11 behind a data migration.

**Consequences:** Writes must go through `VaultStore`, never raw ORM mutations of `vault_items` (bypasses desync the index). `upsert` is full replacement, not patch — read-modify-write to preserve an embedding. Dim-change repair = `upsert` with fresh vectors; no auto-re-embed ships (detection columns + idempotent upsert are the primitives). Reopened if tag-filtered search arrives: tags live in `meta` JSON with no `vault_tags` table, so that item repeats this one-way-door analysis.
