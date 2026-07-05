# Octave — System & Component Architecture

> For full detailed architecture, see [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) and the diagrams in [docs/diagrams/](../../docs/diagrams/).

## Overview

Octave is a local-first agent harness that orchestrates MCP (Model Context Protocol) servers and inference engines. It is built around four core backend components connected to a React frontend, with all data stored in a vector-capable local database.

## Technology Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React (Vite), WebSocket/SSE for real-time |
| Backend | Python, FastAPI, REST + WebSocket |
| Database | SQLite with vec0 extension (default) or PostgreSQL with pgvector |
| Migrations | Alembic |
| Protocol | MCP (JSON-RPC 2.0) via stdio or HTTP/SSE transports |

## System Components

### Frontend (React)

Six view areas mounted on a shared layout shell:

- **Core Layout** — Navigation shell, sidebar, responsive content area
- **Chat Interface** — Message display, input, real-time streaming, conversation history
- **MCP Connector View** — Server list, configuration, tool explorer, manual tool testing
- **Context Manager View** — Vault browser, item editor, running context viewer
- **Agent Manager View** — Agent status dashboard, context panel, result viewer
- **Settings** — Inference engine config, MCP global settings, user preferences

### Backend Subsystems

#### Inference Engine Connector

Pluggable adapter interface for LLM inference engines.

- Abstract adapter interface (OpenAI-compatible REST format)
- Prompt assembly pipeline (system + injected context + user message)
- Streaming response support (SSE/WebSocket)
- Embedding model support (vector generation for Context Manager)
- Model tagging system (capability tags: `thinking`, `coding`, `quick`)
- Health check and fallback mechanism

**Interacts with:** Context Manager (receives context bundles), Agent Manager (receives turn triggers), MCP Connector (requests tool invocations)

#### MCP Connector

Model Context Protocol client implementation.

- JSON-RPC 2.0 transport layer
- stdio (subprocess) and HTTP/SSE (remote) transports
- Server lifecycle manager (start/stop/restart/health)
- Tool discovery and caching
- Tool execution engine (invoke, handle errors, timeouts)
- Configuration persistence (stored in unified database)
- Tool tagging system (internal Octave labels)
- Tool re-naming / re-describing (custom agent-facing names and descriptions)

**Interacts with:** Context Manager (tagged tool discovery), Inference Engine Connector (executes tool calls), Frontend (exposes tool inventory)

#### Context Manager

Central knowledge and context assembly subsystem.

- Context vault data model (skills, prompts, preferences, agent state)
- Vector-capable storage layer (CRUD + vector indexing)
- Tagged MCP tool vault builder (populates vault via tagged tools)
- Context injection engine (rule/trigger-based selection)
- Agent context lifecycle (archival of completed runs)
- Relevance scoring and token budget management
- External database adapter interface (optional MCP override)
- Skill-to-tool/prompt linking and parameter templating
- Conversation-to-vector indexing
- Vector search query interface (semantic similarity search)
- Skill-to-model linking via tags

**Interacts with:** MCP Connector (vault population), Inference Engine Connector (supplies context bundles), Agent Manager (receives completed run context), Frontend (exposes vault)

#### Agent Manager

Agent lifecycle orchestration and message routing.

- Agent lifecycle model (spawn, pause, resume, terminate)
- Agent registry (track agents, IDs, status, context)
- Message router (deliver to correct agent, broadcast)
- Result collector (capture and query agent outputs)
- Inter-agent result sharing
- Priority and scheduling (queue management, resource constraints)

**Interacts with:** Frontend (routes user messages), Inference Engine Connector (triggers inference), Context Manager (sends completed context), Frontend (exposes status/results)

## Data Layer

Single vector-capable database:

- **SQLite + vec0** (default) or **PostgreSQL + pgvector**
- **Alembic** for schema migrations
- Stores: MCP server configs, tool caches, context vault items with embeddings, conversation history, agent run results

## Request Lifecycle

1. User sends message via Chat Interface
2. Frontend forwards to Backend over WebSocket
3. Agent Manager routes to active agent (or spawns new)
4. Context Manager assembles context (vault query, vector search, relevance scoring, token budget)
5. Inference Engine Connector receives context bundle, runs prompt assembly, sends to LLM
6. LLM responds; if tool use requested, MCP Connector resolves and executes tool
7. Agent response streams back to Frontend
8. Agent Manager sends completed run context to Context Manager for archival

## Design Principles

- **Local-first** — All data resides on-prem
- **MCP as universal abstraction** — Every integration connects via MCP
- **Pluggable components** — Engines, transports, databases are swappable
- **Tag-driven wiring** — Tools, models, skills linked via tags, not hard-coded references

## Architecture Diagrams

- [System Architecture](../../docs/diagrams/system-architecture.md) — Component layout and interconnections
- [Request Flow](../../docs/diagrams/request-flow.md) — End-to-end message lifecycle sequence
- [Data Flow](../../docs/diagrams/data-flow.md) — Context, agent, and tool data paths + vault ER model
