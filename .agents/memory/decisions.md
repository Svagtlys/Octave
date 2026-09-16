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
