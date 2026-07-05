# User Request Flow Diagram

## End-to-End Request Lifecycle

Illustrates how a user message travels through Octave from input to response, including context assembly, inference, and MCP tool execution.

```mermaid
sequenceDiagram
  participant User
  participant ChatUI as Chat Interface
  participant Backend as FastAPI Backend
  participant AgentMgr as Agent Manager
  participant ContextMgr as Context Manager
  participant Inference as Inference Connector
  participant MCP as MCP Connector
  participant LLM as Inference Engine
  participant MCPServer as MCP Server

  User->>ChatUI: Types message, clicks Send
  ChatUI->>Backend: POST /messages (WebSocket stream open)
  Backend->>AgentMgr: Route to active agent (or spawn new)
  AgentMgr->>ContextMgr: Request context assembly for this turn

  ContextMgr->>ContextMgr: Query Context Vault (skills, prompts, preferences)
  ContextMgr->>ContextMgr: Vector search conversations for relevant history
  ContextMgr->>ContextMgr: Apply relevance scoring / token budget filter
  ContextMgr-->>AgentMgr: Return assembled context bundle

  AgentMgr->>Inference: Submit context bundle + user message
  Inference->>Inference: Run Prompt Assembly Pipeline
  Inference->>LLM: Send assembled prompt (streaming)
  LLM-->>Inference: Stream tokens back

  alt Tool use requested by LLM
    Inference->>MCP: Request tool invocation (name + arguments)
    MCP->>MCP: Resolve tool name / description mappings
    MCP->>MCPServer: Execute tool via JSON-RPC
    MCPServer-->>MCP: Tool result
    MCP-->>Inference: Return tool result to agent
    Inference->>LLM: Continue generation with tool result
    LLM-->>Inference: Stream final response tokens
  end

  Inference-->>AgentMgr: Return agent response
  AgentMgr->>ContextMgr: Store completed run context for future linking
  AgentMgr-->>Backend: Stream response tokens
  Backend-->>ChatUI: WebSocket/SSE token stream
  ChatUI-->>User: Display streaming response
```

## Inference Loop (Tool Use Cycle)

Shows the iterative loop when the agent requests multiple tool calls within a single turn.

```mermaid
flowchart TD
  A[Agent Sends Prompt to LLM] --> B{LLM Requests Tool?}
  B -->|No| C[Stream Final Response to User]
  B -->|Yes| D[MCP Connector Resolves Tool]
  D --> E[Execute Tool on MCP Server]
  E --> F[Return Tool Result to Agent]
  F --> G[Append Result to Context]
  G --> A
  C --> H[Store Run Context in Vault]
```
