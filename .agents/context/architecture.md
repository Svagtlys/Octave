# Octave Architecture

## Overview

Octave is a local-first agent harness that acts as a proactive personal assistant. It gathers, sorts, stores, and presents knowledge to improve the user's life — both reactively (through a chat interface) and proactively (through scheduled polling and event-driven automation via external tools like n8n).

Octave is intentionally bare-bones: it is the **glue** tying together pluggable MCP (Model Context Protocol) servers. It makes autonomous decisions about what to do and when, but delegates all execution to connected MCP servers.

## System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     External MCP Servers                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────┐   │
│  │ Filesys  │  │  Vector  │  │  Hass    │  │   n8n      │   │
│  │   MCP    │  │  DB MCP  │  │   MCP    │  │   MCP      │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬───────┘   │
└───────┼─────────────┼─────────────┼─────────────┼───────────┘
        │             │             │             │
        ▼             ▼             ▼             ▼
┌─────────────────────────────────────────────────────────────┐
│                      Octave Backend                         │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                    MCP Gateway                       │   │
│  │  - Connection pool (StdIO/HTTP/SSE)                  │   │
│  │  - Tool registry (custom names/descriptions)         │   │
│  │  - Gateway skill loader                              │   │
│  │  - Role tagging (kb_read, kb_write, scheduled, etc.) │   │
│  └──────────────────┬───────────────────────────────────┘   │
│                     │                                       │
│         ┌───────────┴───────────┐                           │
│         ▼                       ▼                           │
│  ┌──────────────┐        ┌──────────────┐                   │
│  │ Context      │───────▶│ Context Vault│                   │
│  │ Assembler    │        │ Manager      │                   │
│  │              │        │ (Indexer)    │                   │
│  │ - Load prefs │        │ - Scan KB    │                   │
│  │ - Load skills│        │ - Parse tags │                   │
│  │ - Retrieve   │        │ - Build vault│                   │
│  │   knowledge  │        │ - Expose     │                   │
│  │ - Attach gw  │        │   rebuild    │                   │
│  │   skills     │        │   tools      │                   │
│  └──────┬───────┘        └──────────────┘                   │
│         ▼                                                   │
│  ┌──────────────┐                                           │
│  │ Reasoning    │                                           │
│  │   Engine     │                                           │
│  │  (LLM Loop)  │                                           │
│  └──────────────┘                                           │
└─────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. MCP Gateway

Manages all MCP server connections and tool routing.

**Responsibilities:**
- Start, stop, restart MCP servers (StdIO, HTTP, SSE transport)
- Maintain tool registry with custom names and descriptions per connection
- Load and cache gateway skills for each MCP
- Allows the user to tag tools with roles for use by Octave internal logic
- Route tool calls to correct MCP connection

**Functional Roles:**

| Role | Description | Example |
|------|-------------|---------|
| `kb_read` | Tools that retrieve knowledge | FS `read_file`, VDB `search` |
| `kb_write` | Tools that persist knowledge | FS `write_file` |

### 2. Context Assembler

Builds the LLM prompt from available knowledge, skills, and preferences.

**Assembly Order:**
1. Base system prompt (Octave identity/instructions or subagent prompt)
2. Auto-loaded preferences (from KB *preferences* index)
3. Auto-loaded skill names and descriptions, or full skill body (from KB *skills* index, filtered by trigger keywords, basic info vs full body determined by settings)
4. Retrieved knowledge (semantic/keyword search via *kb_read* MCP tools)
6. User message or event context

### 3. Context Vault Manager

Indexes the knowledge base and builds the Context Vault.

**Indexed Content:**
- *preferences* — User preferences (auto-injected into system prompt)
- *skills* — Skill metadata (loaded on trigger keyword match or via invocation tool)
- *prompts* — Reusable prompt templates, either for common scenarios or for use in subagents

**Non-Indexed Content:**
- *general* — General knowledge (searched on-demand by LLM via `kb_read` MCPs)

### 4. Reasoning Engine

Orchestrates the LLM interaction loop.

**Flow:**
1. Receive user message or event trigger
2. Context Assembler builds prompt
3. LLM generates response (may include tool calls)
4. MCP Gateway routes tool calls
5. Results injected back into context
6. Repeat until LLM produces final response

## Configuration

Main configuration file: `config/octave.yaml`

```yaml
# LLM Provider
llm:
  provider: "ollama"
  url: "http://localhost:11434"
  model: "qwen3-235b-a22b"
  temperature: 0.7

# MCP Integrations
integrations:
  - name: "filesystem"
    transport: "stdio"
    command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/knowledge_base"]
    roles: ["kb_read", "kb_write"]

  - name: "qdrant"
    transport: "stdio"
    command: ["python", "-m", "mcp_qdrant_server"]
    env:
      QDRANT_URL: "http://localhost:6333"
    roles: ["kb_read"]
```

## Project Structure

```
octave/
├── AGENTS.md
├── .agents/
│   ├── rules/
│   ├── context/
│   ├── memory/
│   └── skills/
├── backend/
│   ├── main.py
│   ├── mcp/
│   ├── context/
│   └── reasoning/
├── frontend/
│   ├── package.json
│   └── src/
├── config/
│   └── octave.yaml
├── compose.yaml
├── Dockerfile
└── docs/
```
