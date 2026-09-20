# Data Flow Diagram

## Context and Agent Data Paths

Shows how data moves between components for context management, agent state, and tool metadata.

```mermaid
flowchart LR
  subgraph Sources["Data Sources"]
    MCPServers["MCP Servers - Tool Schemas"]
    LLM[LLM Embedding Model]
    UserConv[User Conversations]
  end

  subgraph ContextMgr["Context Manager"]
    ToolTag[Tagged Tool Builder]
    Embedder[Embedding Pipeline]
    Vault["(Context Vault - Vector DB)"]
    Injection[Injection Engine]
  end

  subgraph AgentMgr["Agent Manager"]
    Registry[Agent Registry]
    Results[Result Collector]
  end

  subgraph Consumers["Consumers"]
    Inference[Inference Connector]
    UI[Frontend Views]
  end

  MCPServers --> ToolTag
  ToolTag --> Vault
  UserConv --> Embedder
  LLM --> Embedder
  Embedder --> Vault
  AgentMgr --> Results
  Results --> Embedder
  Vault --> Injection
  Injection --> Inference
  Vault --> UI
  Registry --> UI
  Results --> UI
```

## Shipped Schema (v1 — PR #84)

```mermaid
erDiagram
  USERS ||--o{ SESSIONS : "creates"
  USERS ||--o{ PARTICIPANTS : "identity"
  AGENTS ||--o{ PARTICIPANTS : "identity"
  USERS ||--o{ VAULT_ITEMS : "owns"
  SESSIONS ||--o{ SESSION_PARTICIPANTS : "has members"
  PARTICIPANTS ||--o{ SESSION_PARTICIPANTS : "member of"
  SESSIONS ||--o{ EVENTS : "transcript"
  SESSIONS ||--o{ SESSIONS : "spawns sub-sessions"
  PARTICIPANTS ||--o{ EVENTS : "authors"

  VAULT_ITEM {
    string id PK
    string user_id FK
    string kind "skill | prompt | preference | run_summary | run_record"
    string name
    text content "source of truth"
    json metadata "tags live here"
    blob embedding "float32 little-endian, nullable"
    string embedding_model "nullable"
    int embedding_dim "nullable"
  }

  SESSION {
    string id PK
    string created_by_user_id FK "single owner"
    string parent_session_id FK "nullable lineage"
    string status "active | waiting | completed | failed | cancelled"
    string title
  }

  EVENT {
    string id PK
    string session_id FK
    int seq "monotonic per session"
    string kind "user_message | assistant_message | tool_call | tool_result | system"
    string author_participant_id FK "nullable for system"
    string target_participant_id "nullable = broadcast"
    json payload
  }

  PARTICIPANT {
    string id PK
    string user_id FK "nullable"
    string agent_id FK "nullable, exactly one set"
    string label
  }
```

Planned, not yet created (additive migrations in their consumer work items):
`SKILL_LINK`, `TOOL_TAG`, `MODEL_TAG`, `TOOLS`, `INJECTION_RULES`.

Vault item structure conventions (`metadata` tags, links, skill params, run
provenance) are documented in the
[context vault data model design](../../.agents/specs/2026-09-20-context-vault-data-model-design.md).
Run archival is two-tier: `run_summary` (Tier 1 — "which run?") then
`run_record` (Tier 2 — verbatim chunks), linked by `metadata.session_id`.
