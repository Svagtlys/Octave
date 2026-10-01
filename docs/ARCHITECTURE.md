# Octave — System Architecture

## Overview

Octave is a local-first agent harness that orchestrates MCP (Model Context Protocol) servers and inference engines to act as a proactive personal assistant. The system is built around four core backend components connected to a React frontend, with all data stored in a vector-capable local database.

**Architecture diagrams:**
- [System Architecture](diagrams/system-architecture.md) — Component layout and interconnections
- [Request Flow](diagrams/request-flow.md) — End-to-end user message lifecycle
- [Data Flow](diagrams/data-flow.md) — Context, agent, and tool data paths

---

## Component Overview

### Frontend (React)

The frontend provides five view areas mounted on a shared layout shell. All views communicate with the backend via REST APIs and WebSocket/SSE streams.

| View | Purpose |
|------|---------|
| **Core Layout** | Navigation shell, sidebar, responsive content area, shared component library |
| **Chat Interface** | Message display, input with attachments, real-time streaming, conversation history |
| **MCP Connector View** | Server list with status, add/edit/remove configuration, tool explorer, manual tool testing |
| **Context Manager View** | Vault browser (skills/prompts/preferences), item editor, running context viewer |
| **Agent Manager View** | Agent status dashboard, active agent context panel, completed run result viewer |
| **Settings** | Inference engine configuration, MCP global settings, user preferences |

### Backend (Python / FastAPI)

The backend exposes REST endpoints and WebSocket connections. It is structured around four pluggable subsystems.

---

### Inference Engine Connector

Manages communication with external or local LLM inference engines via a pluggable adapter interface.

**Responsibilities:**
- **Engine Adapter Interface** — Abstract base class enabling multiple inference backends (OpenAI-compatible REST, local models, etc.)
- **Prompt Assembly Pipeline** — Receives assembled context from the Context Manager, constructs the final prompt (system instructions + injected context + user message), and sends to the selected engine
- **Streaming Response** — Supports real-time token streaming back to the frontend via SSE/WebSocket
- **Embedding Model Support** — Generates vector embeddings for the Context Manager's indexing pipeline
- **Model Tagging System** — Labels models with capability tags (e.g., `thinking`, `coding`, `quick`) so the Context Manager can resolve skill-to-model requirements at runtime
- **Health Check & Fallback** — Monitors engine availability and switches to fallback engines on failure

**Key interactions:**
- Receives context bundles from the **Context Manager**
- Receives agent turn triggers from the **Agent Manager**
- Requests tool invocations through the **MCP Connector** when the LLM outputs tool calls

**Implemented — adapter contract (issue #8):** the `octave.inference` package exposes the
`InferenceAdapter` ABC (`complete`, `stream`, `embed`, `list_models`), an `AdapterRegistry`
resolving adapters by name or `module.path:ClassName` import string, env-backed
`AdapterConfig`, and a built-in `OpenAIAdapter` for OpenAI-dialect servers (Ollama, vLLM,
llama.cpp server, LM Studio). The `openai` SDK is quarantined to `openai_adapter.py`; SDK
errors translate to Octave types. All adapters — including third-party plugins — are gated
by a shared conformance test suite. Design:
[`.agents/specs/2026-09-08-inference-adapter-interface-design.md`](../.agents/specs/2026-09-08-inference-adapter-interface-design.md).

---

### MCP Connector

Implements the Model Context Protocol client, enabling Octave to discover, manage, and invoke tools across pluggable MCP servers.

**Responsibilities:**
- **JSON-RPC 2.0 Client Core** — Typed `McpClient` façade (`octave.mcp`) over the official `mcp` Python SDK: framing, request-ID correlation, initialize handshake, error translation
- **Transport Support** — stdio (subprocess) and Streamable HTTP behind `open_transport`; legacy SSE deliberately not wrapped
- **Connection Lifecycle** — Start and manual restart (`McpClient.restart()`) with `is_connected` liveness; subprocess exit detected via transport-stream monitoring (fail-fast `McpConnectionError`). Auto-restart policy and health monitoring land with the server lifecycle manager (roadmap #4) *(shipped: `octave.mcp.manager` — per-server supervisors, auto-restart with backoff + crash-loop detection, probe-on-timeout health, PR #93)*
- **Tool Discovery & Caching** — Fetches tool schemas and descriptions from servers; caches for fast lookup *(shipped: `octave.mcp.registry.ToolRegistry` — fleet-wide inventory cache with event-driven invalidation (restart drift, `tools/list_changed` notifications, warm-up + lazy refresh), PR #98)*
- **Tool Execution Engine** — Invokes tools with arguments, handles responses and errors, enforces timeouts *(shipped: `ToolRegistry.call_tool` — `(server_id, tool_name)` surface forwarding to `McpClient.call_tool`; timeout/RPC/connection semantics stay the client's, PR #98)*
- **Schema Translation** — Converts MCP `inputSchema` into the provider-native `tools` array format with `mcp__<server>__<tool>` dedupe and reverse routing *(shipped: `octave.tools.translate_tools` — pure translation into `octave.inference` `ToolDefinition`s carried on `CompletionRequest.tools`, PR #100; response-side `tool_calls` parsing shipped with the agent loop, PR #103)*
- **Configuration Persistence** — Stores server connection configs in the unified database
- **Tool Tagging System** — Labels tools with internal Octave tags (e.g., `context_retrieval`, `file_operations`) used by the Context Manager for vault population
- **Tool Re-naming / Re-describing** — Maps custom agent-facing names and descriptions to underlying MCP tool identifiers, improving clarity for the agent without modifying the MCP server

**Key interactions:**
- Provides tagged tool discovery to the **Context Manager** (vault population pipeline)
- Executes tool calls requested by the **Inference Engine Connector** during agent turns
- Exposes tool inventory to the **MCP Connector View** in the frontend

---

### Context Manager

Central knowledge and context assembly subsystem. Manages the context vault — a vector-indexed store of skills, prompts, preferences, and agent state — and assembles relevant context for each agent turn.

**Responsibilities:**
- **Context Vault Data Model** — Schemas for skills, prompts, preferences, and agent state items
- **Vector-Capable Storage Layer** — CRUD operations and vector indexing via SQLite/vec0 or PostgreSQL/pgvector
- **Tagged MCP Tool Vault Builder** — Discovers and invokes tagged MCP tools to populate the vault with skills, prompts, and preferences *(depends on MCP Connector tool tagging)*
- **Context Injection Engine** — Selects relevant vault items based on rules and triggers for each agent turn
- **Agent Context Lifecycle** — Receives full context from completed agent runs (via Agent Manager), embeds into vector DB for future linked runs *(depends on Agent Manager)*
- **Relevance Scoring / Token Budget** — Filters and ranks context items within token limits
- **External Database Adapter** — Optional interface for MCP-provided database overrides
- **Skill-to-Tool/Prompt Linking** — Associates skills with specific tools or prompts
- **Skill Parameter Templating** — Auto-replaces placeholder parameters in skill definitions with linked tool/prompt names
- **Conversation-to-Vector Indexing** — Embeds conversation messages into the vector DB for semantic search
- **Vector Search Query Interface** — Semantic similarity search across conversations and vault items
- **Skill-to-Model Linking** — Attaches required model capability tags to skills; resolves to matching models at runtime via the Inference Engine Connector's model tagging system

**Implemented — pull-only context archival (issue #35):** the `octave.context`
package is the Context Manager's first service module. `ContextArchiver.archive(session_id)`
reconstructs turn brackets from the session transcript (`brackets.py`: a bracket spans
from the previous anchor event to an agent reply; failed turns close nothing), renders
each verbatim into one `transcript_chunk` vault item (`chunks.py`, full content — distinct
from the summarizer's digest budget; `max_chars` safety split at whitespace), and embeds
Tier-1 `session_summary` items via read-modify-write (prose owned by
`octave.agent.summaries`, embeddings owned here). Idempotence falls out of deterministic
ids (`transcript_chunk:<session>:<seq_start>-<seq_end>`) and "skip iff embedded by the
current model". Plane ban enforced by AST guard: `octave.context` never imports
`octave.agent` (binding resolution and CM-local errors are deliberate mirrors). The
archiver embeds only — never generates prose; `max_chars` derives from the embedding
model's input context window (conservative default 6000). Library-only: routes/lifespan
wiring arrives with Integration & Testing #1. Design:
[`.agents/specs/2026-09-30-agent-context-lifecycle-design.md`](../.agents/specs/2026-09-30-agent-context-lifecycle-design.md).

**Implemented — context injection engine (issue #34):** `ContextInjector` selects standing
vault context (prompt / preferences / skills) per agent — owner-scoped, dual-tagged (reserved
`global` tag + intersection with the agent's `assignments.effective_tags`) additively unioned
with explicit by-name assignments — and injects it once per (session, agent) as a durable
`context_injection` transcript event at session start (`ensure_injected`, idempotent; membership
in the target session required). Zero migrations: `EventKind.CONTEXT_INJECTION` plus
`AgentAssignments.tags` / `preference_names` in the existing JSON column; payload validated on
append via `EventStore`. Corrupt JSON degrades to untagged/empty with a warning. The router
skips the new kind (chat history untouched); the archiver treats injection events as anchors.
`every_turn` cadence deferred post-1.0.0. Design:
[`.agents/specs/2026-10-01-context-injection-engine-design.md`](../.agents/specs/2026-10-01-context-injection-engine-design.md).

**Key interactions:**
- Populates vault using tagged tools from the **MCP Connector**
- Supplies assembled context bundles to the **Inference Engine Connector**
- Receives completed agent run context from the **Agent Manager**
- Exposes vault contents to the **Context Manager View** in the frontend

---

### Agent Manager

Orchestrates agent lifecycles, routes messages between agents and subsystems, and collects results for inter-agent sharing and context archival.

**Responsibilities:**
- **Agent Lifecycle Model** — Definition `active|paused` gate; instance spawn → idle ⇄ active → destroy
- **Agent Registry** — Tracks running agents, their IDs, status, and assigned context
- **Message Router** — Delivers incoming messages to the correct agent; supports broadcast
- **Result Collector** — Captures agent outputs and makes them queryable
- **Inter-Agent Result Sharing** — Allows agents to request and receive results from other agents
- **Priority & Scheduling** — Queue management and resource constraint enforcement

**Key interactions:**
- Routes user messages from the **Frontend** to active agents
- Triggers inference cycles through the **Inference Engine Connector**
- Sends completed run context to the **Context Manager** for archival
- Exposes agent status and results to the **Agent Manager View** in the frontend

**Implemented — tool-use orchestration loop (issue #79):** the `octave.agent` package is
the composition layer and future Agent Manager home. `ToolLoop` drives the
reason → act → observe cycle: detect `CompletionResult.tool_calls`, resolve exposed
names through `ProviderToolset.routes` (#78), execute sequentially via the
`ToolExecutor` Protocol, append provider-invariant tool messages, and re-invoke until a
final answer (round limit `max_tool_rounds` raises `ToolLoopLimitError` with the partial
transcript). `McpToolExecutor` wraps `ToolRegistry`, converting `McpError` failures into
model-correctable error tool messages. `octave.inference` gained the OpenAI-dialect
vocabulary for this (`ToolCall`, `role="tool"`, tool fields on `Message`). Library-only:
no routes/lifespan wiring yet — composition arrives with Integration & Testing #1. The
`openai`/`mcp` SDKs stay quarantined from the package (AST guard). Design:
[`.agents/specs/2026-09-25-tool-use-orchestration-loop-design.md`](../.agents/specs/2026-09-25-tool-use-orchestration-loop-design.md).

**Implemented — agent lifecycle model (issue #25):** definitions (`agents`) carry a
definition-level `active|paused` gate, a `model_binding` (tag or explicit
provider-model pair) and `assignments` (named vault-item references); instances
(`agent_instances`) are ephemeral one-definition-per-session rows, hard-deleted on
destroy and carrying no context — the session transcript and the vault own it.
`AgentInstanceManager` (`octave.agent.instances`) is the single write path, following
the `VaultStore` convention (caller-supplied `AsyncSession`, never commits): `spawn`
ensures participant + membership (re-invite resets `left_at`) and fails loud on
paused definitions, terminal sessions, duplicate bindings, and missing/malformed
model bindings; `begin_turn` claims the idle→active turn mutex atomically; `destroy`
is a plain delete (archival rides the turn boundary, decoupled by design);
`reconcile` resets stale `active` rows to idle after unclean shutdown.
`resolve_model` resolves a binding to a concrete (adapter, model) pair at turn start,
fail-loud on tag misses (tag→model lookup lands with Inference #7). Library-only: no
routes/lifespan wiring yet. Design:
[`.agents/specs/2026-09-27-agent-lifecycle-model-design.md`](../.agents/specs/2026-09-27-agent-lifecycle-model-design.md).

**Implemented — agent registry (issue #26):** `AgentRegistry` (`octave.agent.registry`)
is the read-only source-of-truth view over `agent_instances ⋈ agents` — `list_instances`
(filters: `agent_id`/`session_id`/instance `status`, AND-composed; deterministic
`created_at, id` ordering), `get_instance` (raises `InstanceNotFoundError`), and
`count_by_status` (zero-filled per-enum dashboard counter). Returns frozen
`RunningAgent` read models; same `VaultStore` convention (caller-supplied
`AsyncSession`, never commits, never mutates — `AgentInstanceManager` stays the only
write path). Malformed `assignments` JSON degrades to empty with a warning (reporting
surface); bad status enums fail loud. Library-only: no routes. Design:
[`.agents/specs/2026-09-27-agent-registry-design.md`](../.agents/specs/2026-09-27-agent-registry-design.md).

**Implemented — agent message routing (issue #27):** `MessageRouter`
(`octave.agent.router`) is the session-level turn-taking driver: `deliver()` appends
the user message (author must be a current member — `NotAMemberError` otherwise), then
loops — the injected `TurnDecider` names the next speaker from the idle-instance
roster, `begin_turn` claims the #25 mutex, the injected `TurnRunner` port produces the
reply, the reply is appended as `assistant_message`, the turn is released — until
`AWAIT_USER`, the hop limit (`max_agent_turns`), or a runner failure (`ERROR`; failed
turn lands idle + `system` event). Delivery is via the shared transcript: no per-agent
queues; the runner reads the transcript as input. `LlmTurnDecider`
(`octave.agent.decider`) is the default strategy — a 1:1 fast path with zero LLM calls,
roster-validated JSON choice for multi-agent sessions; the driver retries a confused
referee once, then hands control to the human. `EventStore` (`octave.db.event_store`)
owns transcript appends: gap-free per-session `seq` with savepoint retry on collision
(uq constraint is the backstop), per-kind payload validation. `TurnRunner` stays a
port — the real runner (context assembly + `ToolLoop`) is Integration #1's composition.
Library-only: no routes; one `deliver()` is one caller-owned transaction (never
commits). Design:
[`.agents/specs/2026-09-28-agent-message-routing-design.md`](../.agents/specs/2026-09-28-agent-message-routing-design.md).

**Implemented — session result collection (issue #28):** `SessionSummarizer`
(`octave.agent.summaries`) produces the Tier-1 headline: one LLM-written summary
per session (what was done + end result), cached as a `SESSION_SUMMARY` vault item
under the deterministic id `session_summary:<session_id>`. Generation is
out-of-band — never inside `MessageRouter.deliver()`; the LLM call precedes any DB
write. Freshness is computed, never stored: `collect()` returns the cached item when
its `covered_seq` equals the transcript's latest seq, regenerates when the
transcript grew, and `peek()` reads without ever calling the adapter. Model
selection reuses `resolve_model`/`ModelBinding` (#25); the model-facing material
renders through a `TranscriptDigest` protocol (`HeadTailDigest` default; search-
and map-reduce strategies are filed follow-ups). The `events` transcript remains
canonical — vault summaries are derived, and `VaultStore` conventions hold (never
commits). Vault kinds renamed `run_summary`/`run_record` →
`session_summary`/`transcript_chunk` before any row shipped (ADR 2026-09-29).
Library-only: no routes; result-viewer UI wiring is a filed follow-up. Design:
[`.agents/specs/2026-09-29-agent-result-collection-design.md`](../.agents/specs/2026-09-29-agent-result-collection-design.md).

---

## Data Layer

Single vector-capable database behind an adapter seam (`octave.db`), mirroring
the inference adapter pattern:

- **`DbAdapter` ABC + registry** — engine selection by name or import string;
  `sqlite` (SQLite + vec0) is the only adapter registered today, `pgvector`
  will self-register via the plugin path when it lands
- **Explicit transaction control** (`octave.db._bootstrap.attach_transaction_control`)
  — SQLite connections disable pysqlite's implicit-BEGIN mode and emit explicit
  `BEGIN`; without it `RELEASE SAVEPOINT` acts as a commit, silently breaking the
  "stores never commit; callers own the transaction boundary" convention that
  `EventStore`'s seq-retry (and every store) depends on (issue #27)
- **Alembic** for schema migrations, run programmatically via
  `octave.db.migrations.upgrade()` — auto-applied on app startup via
  `octave.db.lifespan.db_lifespan` unless `OCTAVE_DB_AUTO_MIGRATE=false`
  (then startup verifies the DB is migrated and fails fast if not)
- **Transcript vocabulary**: `sessions` / `session_participants` / `events`
  (`events.kind` is a typed, app-validated enum — not every entry is text),
  with `participants` as the identity supertype over `users` and `agents`
- **Vector index is adapter-private**: `vec_vault_items_<N>` is a dim-suffixed
  vec0 virtual table, not Alembic-managed; `vault_items.embedding` is the
  engine-neutral cache and `content` is the source of truth
- Stores: MCP server configs, vault items with embeddings, session transcripts,
  agent registry entries (`agents`: `model_binding`/`assignments` JSON,
  `active|paused` gate) and live instance bindings (`agent_instances`: unique
  agent×session, RESTRICT on definition delete, CASCADE on session delete)

---

## Request Lifecycle

The end-to-end flow for a user message:

1. **User sends message** via the Chat Interface
2. **Frontend** forwards message to the Backend over WebSocket
3. **Agent Manager** routes message to the active agent (or spawns a new one)
4. **Context Manager** assembles context:
   - Queries the Context Vault for relevant skills, prompts, and preferences
   - Performs vector search across conversation history
   - Applies relevance scoring and token budget filtering
5. **Inference Engine Connector** receives the context bundle:
   - Runs the Prompt Assembly Pipeline (system + context + user message)
   - Sends assembled prompt to the LLM via the selected Engine Adapter
6. **LLM responds** — if tool use is requested:
   - **MCP Connector** resolves the tool (applying name/description mappings)
   - Tool is executed on the target MCP Server
   - Result is returned to the agent, and generation continues
7. **Agent response** is streamed back through the Backend to the Frontend
8. **Agent Manager** sends completed run context to the **Context Manager** for archival

See [Request Flow](diagrams/request-flow.md) for the visual sequence diagram.

---

## Design Principles

- **Local-first** — All data resides on-prem. No mandatory external API calls beyond the user-configured inference engine.
- **MCP as universal abstraction** — Every external integration connects via MCP. No hardcoded integrations.
- **Pluggable components** — Inference engines, MCP transports, and database backends are all swappable.
- **Tag-driven wiring** — Tools, models, and skills are linked through a tag system rather than hard-coded references, enabling runtime resolution and flexibility.
