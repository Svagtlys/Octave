# Octave — Development Todo List

## Project Foundation

- [ ] 1. Initialize Python backend project structure (virtual env, dependencies, FastAPI/Flask server)
- [ ] 2. Initialize React frontend project structure (Vite/Create React App, routing setup)
- [ ] 3. Establish backend-frontend communication layer (REST API + WebSocket for real-time chat)
- [ ] 4. Set up vector-capable database schema and ORM (SQLite with vec0 extension or PostgreSQL with pgvector; Alembic for migrations)

---

## UI — Core Layout

- [ ] 1. Build main application shell (navigation, sidebar, content area)
- [ ] 2. Create responsive layout components and shared UI component library

---

## UI — Chat Interface

- [ ] 1. Build chat message display component (user/agent messages, timestamps)
- [ ] 2. Implement chat input component (text area, send button, attachment support)
- [ ] 3. Create real-time message streaming (SSE/WebSocket integration for agent responses)
- [ ] 4. Add conversation history management (list, switch, delete conversations)

---

## UI — MCP Connector View

- [ ] 1. Build MCP server list view (connected servers, status indicators)
- [ ] 2. Create MCP server add/edit/remove UI (configuration form, connection testing)
- [ ] 3. Build MCP tool explorer (browse available tools per server, view tool schemas)
- [ ] 4. Add MCP tool invocation interface (manual tool testing/debugging UI)

---

## UI — Context Manager View

- [ ] 1. Build context vault browser (skills, prompts, preferences categories)
- [ ] 2. Create context item editor (CRUD for skills, prompts, preferences)
- [ ] 3. Build agent context viewer (current running context, injected items)

---

## UI — Agent Manager View

- [ ] 1. Build agent status dashboard (list running agents, states, progress indicators)
- [ ] 2. Add agent panel to chat interface (show active agent context alongside conversation)
- [ ] 3. Create agent result viewer (browse outputs from completed agent runs)

---

## UI — Settings

- [ ] 1. Create inference engine settings panel (endpoint URL, API key, model selection)
- [ ] 2. Build MCP global settings (default transport, timeout, retry policy)
- [ ] 3. Create user preferences panel (theme, language, default models)

---

## Inference Engine Connector

- [ ] 1. Design pluggable inference engine interface (abstract base class for engine adapters)
- [ ] 2. Implement generic REST-based inference adapter (OpenAI-compatible API format)
- [ ] 3. Add streaming response support for real-time token output
- [ ] 4. Build prompt assembly pipeline (receives assembled context from Context Manager, constructs final prompt for inference)
- [ ] 5. Add engine health check and fallback mechanism
- [ ] 6. Add embedding model support (generate vector embeddings for context indexing and semantic search)
- [ ] 7. Implement model tagging system (label models with capability tags, e.g., "thinking", "coding", "quick")

---

## MCP Connector

- [ ] 1. Implement MCP client core (JSON-RPC 2.0 transport layer)
- [ ] 2. Add MCP stdio transport support (spawn subprocess servers)
- [ ] 3. Add MCP HTTP/SSE transport support (remote server connections)
- [ ] 4. Build server lifecycle manager (start, stop, restart, health monitoring)
- [ ] 5. Implement tool discovery and caching (fetch tools, schemas, descriptions)
- [ ] 6. Create tool execution engine (invoke tools, handle responses/errors, timeouts)
- [ ] 7. Add MCP server configuration persistence (store server configs in the unified database)
- [ ] 8. Implement MCP tool tagging system (label tools for internal Octave use, e.g., context retrieval, file operations)
- [ ] 9. Add tool re-naming support (map custom names to underlying MCP tool names for agent-facing clarity)
- [ ] 10. Add tool re-describing support (override tool descriptions for agents while preserving original MCP mapping)

---

## Context Manager

- [ ] 1. Design context vault data model (skills, prompts, preferences, agent state schemas)
- [ ] 2. Implement vector-capable storage layer for context vault (CRUD operations, queries, vector indexing)
- [ ] 3. Build context vault using tagged MCP tools (discover and invoke tagged tools to populate skills, prompts, and preferences into the vault) [Depends on: MCP Connector #8–10]
- [ ] 4. Create context injection engine (select relevant context items based on rules/triggers)
- [ ] 5. Implement agent context lifecycle (receive full context from completed agent runs via Agent Manager, embed into vector DB for future linked agent runs to query) [Depends on: Agent Manager #1–4]
- [ ] 6. Add context relevance scoring or filtering (token budget management, priority ranking)
- [ ] 7. Build external database adapter interface (optional MCP-provided database override)
- [ ] 8. Implement skill-to-tool/prompt linking (associate skills with specific tools or prompts)
- [ ] 9. Add skill parameter templating (auto-replace placeholder parameters in skills with linked tool/prompt names)
- [ ] 10. Add conversation-to-vector indexing (convert conversation messages into embeddings stored in the vector DB for semantic search)
- [ ] 11. Build vector search query interface (allow agents and context manager to perform semantic similarity search across conversations and vault items)
- [ ] 12. Add skill-to-model linking via tags (attach required model tags to skills; resolve to matching models at runtime)

---

## Agent Manager

- [ ] 1. Design agent lifecycle model (spawn, pause, resume, terminate states)
- [ ] 2. Build agent registry (track running agents, their IDs, status, and assigned context)
- [ ] 3. Implement agent message routing (deliver messages to correct agent, broadcast when needed)
- [ ] 4. Create agent result collection (capture agent outputs and make them queryable)
- [ ] 5. Build inter-agent result sharing (allow agents to request and receive results from other agents)
- [ ] 6. Add agent priority and scheduling (queue management, resource constraints)

---

## Integration & Testing

- [ ] 1. Wire end-to-end flow: User message → Context assembly → Inference → MCP tool calls → Response
- [ ] 2. Create integration tests for inference connector
- [ ] 3. Create integration tests for MCP connector (mock servers)
- [ ] 4. Create integration tests for context injection pipeline
- [ ] 5. Build basic smoke tests for UI components
- [ ] 6. Create integration tests for agent manager (multi-agent workflows)

---

## Summary

| Area | Items |
|------|-------|
| Project Foundation | 4 |
| UI — Core Layout | 2 |
| UI — Chat Interface | 4 |
| UI — MCP Connector View | 4 |
| UI — Context Manager View | 3 |
| UI — Settings | 3 |
| UI — Agent Manager View | 3 |
| Inference Engine Connector | 7 |
| MCP Connector | 10 |
| Context Manager | 12 |
| Agent Manager | 6 |
| Integration & Testing | 6 |
| **Total** | **64** |
