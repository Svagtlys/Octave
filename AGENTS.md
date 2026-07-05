# AGENTS.md

## Identity

You are an AI assistant working on **Octave** — a local-first agent harness that acts as a proactive personal assistant. Octave is the glue tying together pluggable MCP (Model Context Protocol) servers, making autonomous decisions but delegating all execution to connected MCP servers.

**Core Principles:**
- **Local-first:** All data stays on-prem. Minimal external API calls.
- **MCP as the universal abstraction:** Every integration connects via MCP.
- **Swappable components:** No built-in scheduler, file watcher, or vector DB. Everything is an MCP server.
- **User-owned knowledge:** KB lives outside Octave, managed by MCP servers.

## Context Routing

- **Behavioral rules:** READ `.agents/rules/` for coding standards and communication guidelines.
- **Project architecture:** READ `.agents/context/architecture.md` for system design and component overview.
- **Active specs:** CHECK `.agents/specs` for current feature requirements and implementation plans.
- **Past decisions:** CONSULT `.agents/memory/decisions.md` to ensure consistency with prior architectural choices.
- **User preferences:** CONSULT `.agents/memory/user.md` for learned workflow preferences.
- **Executable skills:** CHECK `.agents/skills/` for project-specific capabilities.

## Capabilities

- You may execute scripts found in `.agents/skills/` to validate your work.
