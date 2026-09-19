# Octave

> [!IMPORTANT]
> **Octave is an LLM-developed project.** Code is written by AI agents under the direction of a human software engineer, who reviews all logic and makes the architectural decisions.

> [!WARNING]
> **Not accepting external pull requests until v1.0.0.** The base architecture of Octave is still being decided, and these foundational decisions must be made with the long-term future of the repo in mind — consistency of design matters more than contribution volume at this stage. Please open issues instead: feedback, bug reports, and ideas are welcome and will shape the direction of the project.

## What is Octave?

Octave is a **local-first agent harness** that acts as a proactive personal assistant. It is the glue layer that orchestrates pluggable [MCP (Model Context Protocol)](https://modelcontextprotocol.io) servers and inference engines, making autonomous decisions while delegating all execution to connected MCP servers.

**Core principles:**

- **Local-first** — all data stays on-prem. No mandatory external API calls beyond your own configured inference engine.
- **Swappable components** — no built-in scheduler, file watcher, or vector DB. Everything is a pluggable adapter or an MCP server.
- **User-owned knowledge** — your knowledge base lives outside Octave, managed by your chosen MCP servers.

## Architecture at a Glance

Octave pairs a **React frontend** with a **Python / FastAPI backend** structured around four pluggable subsystems, all backed by a single vector-capable local database (SQLite + vec0 today, pgvector pluggable later):

| Component | Role |
|-----------|------|
| **Inference Engine Connector** | Pluggable LLM adapters (OpenAI-compatible REST), prompt assembly, streaming, embeddings, model tagging |
| **MCP Connector** | MCP client (JSON-RPC 2.0) over stdio and Streamable HTTP, server lifecycle management, tool discovery and execution |
| **Context Manager** | Vector-indexed context vault of skills, prompts, and preferences; context injection and relevance scoring |
| **Agent Manager** | Agent lifecycles, message routing, result collection, and inter-agent sharing |

Full details in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), with diagrams in [`docs/diagrams/`](docs/diagrams/).

## Roadmap

Octave is pre-1.0. Progress is tracked in [`docs/TODO.md`](docs/TODO.md) and [GitHub issues](https://github.com/Svagtlys/Octave/issues). The current state:

**Done**
- Project scaffold — Python backend (FastAPI) + React frontend (Vite), Docker-based dev environment
- Backend–frontend communication layer (REST + WebSocket)
- Vector-capable database layer (`octave.db`) — adapter seam, Alembic migrations, sessions/events transcript schema
- Inference adapter interface + OpenAI-dialect adapter with conformance test suite
- MCP client core (JSON-RPC 2.0) with stdio transport and subprocess lifecycle handling
- MCP server lifecycle manager — per-server supervisors, auto-restart with backoff, crash-loop detection, health probes

**Next**
- MCP tool discovery, caching, and execution engine; configuration persistence
- End-to-end chat flow: user message → context assembly → inference → MCP tool calls → streamed response
- Context vault data model and vector storage layer
- Core UI: application shell, chat interface, MCP connector view

**Later (toward 1.0.0)**
- Context injection engine, relevance scoring, and token budgeting
- Agent manager — lifecycles, registry, message routing, result sharing
- Skill/prompt/preference linking, tagging, and templating
- HTTP/SSE transport config UI, pgvector adapter, multi-agent workflows

## Development

See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for setup instructions.

## License

[AGPL-3.0-only](LICENSE)
