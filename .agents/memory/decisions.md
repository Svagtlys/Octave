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
