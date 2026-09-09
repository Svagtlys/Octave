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
- **JSON-RPC 2.0 Client Core** — Transport-agnostic RPC layer
- **Transport Support** — stdio (subprocess) and HTTP/SSE (remote) transports
- **Server Lifecycle Manager** — Start, stop, restart, and health-monitor connected MCP servers
- **Tool Discovery & Caching** — Fetches tool schemas and descriptions from servers; caches for fast lookup
- **Tool Execution Engine** — Invokes tools with arguments, handles responses and errors, enforces timeouts
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

**Key interactions:**
- Populates vault using tagged tools from the **MCP Connector**
- Supplies assembled context bundles to the **Inference Engine Connector**
- Receives completed agent run context from the **Agent Manager**
- Exposes vault contents to the **Context Manager View** in the frontend

---

### Agent Manager

Orchestrates agent lifecycles, routes messages between agents and subsystems, and collects results for inter-agent sharing and context archival.

**Responsibilities:**
- **Agent Lifecycle Model** — Spawn, pause, resume, and terminate states
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

---

## Data Layer

A single vector-capable database serves as the unified storage backend:

- **SQLite with vec0 extension** — Default lightweight local option
- **PostgreSQL with pgvector** — Alternative for higher-scale deployments
- **Alembic** — Schema migration management

**Stored data:**
- MCP server configurations and tool caches
- Context vault items (skills, prompts, preferences, agent state) with vector embeddings
- Conversation history
- Agent run results

An optional external database adapter interface in the Context Manager allows MCP servers to provide database overrides.

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
