# Glossary

Shared domain vocabulary for Octave. Terms defined here are canonical; avoid
synonyms the glossary explicitly disfavors. Entries are added lazily when a
term actually gets resolved (via triage, grilling, or design work).

## Knowledge Store

**Context Vault**:
The store of standing context items — prompts, preferences, skills, session
summaries, transcript chunks. External knowledge lives *outside* the vault
and flows in via MCP (live query) or user-directed import.

**Gateway skill**:
A skill that documents **how to use a capability available to the agent** —
an MCP tool, a sub-agent, etc. — enhanced guidance beyond what the capability's
own description carries. Distinct from standing skills (prompts/preferences),
which are assigned to agents by tags or names.

- **Derived availability:** a gateway skill is available to an agent iff its
  linked capability is available to that agent — whether the capability is
  injected automatically, context-injected, or searchable (see *injected vs
  searchable tools* in #22). Availability is never assigned directly to the
  skill.
- **Progressive disclosure:** the agent sees the gateway skill's summary in
  context and loads its full text through a dedicated read tool.

_Avoid_: "tool skill" (initial draft term in #38 triage; too narrow — the
linked capability may be a tool, sub-agent, or other agent-facing surface).

_Drafted in #38 triage (2026-10-18, renamed per maintainer note same day);
storage shape (dedicated `VaultKind` vs `meta` marker) and the read-tool
contract remain open._

**Injected vs searchable tools**:

_(to be defined — settling this is the open design work of #22)_

## UI

**Component Catalog**:
The set of host-native UI primitives an agent may address when generating
interface descriptions — name → component + props schema. The shared
primitives library (#7) is the seed of Octave's catalog; the registry layer
is deferred to a dedicated generative-UI ticket.
_Avoid_: "widget library" (implies third-party/pre-built, not agent-addressable)

**Elicitation**:
The MCP mechanism by which a server requests structured input from the user
mid-tool-call via a flat JSON Schema, answered through the client with
accept/decline/cancel. Octave renders elicitation as a form built from the
Component Catalog.
_Avoid_: "prompt", "form request" (both collide with vault prompts and
generic HTML forms)
