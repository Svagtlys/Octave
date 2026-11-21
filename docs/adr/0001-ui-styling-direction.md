# UI styling direction: bespoke primitives, headless a11y, CSS Modules

Octave's frontend had no styling system beyond inline styles over `theme.ts` color tokens, and every downstream UI ticket (#43–59, #119) would be shaped by the direction chosen for the shared primitives library (#7). We decided: **bespoke visual components driven by our own design tokens**, expressed as **CSS Modules** with tokens mirrored into CSS custom properties (dark-only for now, light mode kept as a future drop-in), plus **Radix UI headless primitives** for the genuinely hard interactive behaviors (modal focus trapping, tooltip hover intent, dropdown/menu keyboard navigation). Trivial components (Button, Input, Card, Badge) are hand-built outright — no opinionated component framework (MUI et al.).

## Considered Options

- **MUI (or another styled framework)** — fastest path to a complete UI, but imposes the Material design language, fighting Octave's established dark palette; theming it back to our tokens costs more than building bespoke, and its oversized widget set would make any future LLM-facing component catalog visually foreign and hard to constrain.
- **Fully bespoke including accessibility** — keeps zero dependencies, but hand-rolling focus traps, ARIA wiring, and hover/collision behavior for modals, tooltips, and menus is error-prone in exactly the components users interact with most.
- **shadcn/ui (Radix + Tailwind)** — viable middle ground, but adds Tailwind's whole utility vocabulary on top of an existing token system; Radix directly (with our CSS Modules) keeps the dependency surface minimal.

## Consequences

- Primitives in #7 are specced to cover **MCP elicitation flat-forms** (string/number/boolean/enum inputs with per-field validation) so the inbound-request handler registry (#82) can render elicitation prompts with no primitive retrofits.
- The primitives library is the future **component catalog** for declarative agent-generated UI (A2UI-style). The name→component registry with props schemas is deliberately deferred to a dedicated generative-UI ticket — #7 builds clean, well-typed primitives as its foundation. Raw LLM-authored HTML/JS is rejected as the primary path (arbitrary code execution, style drift, no structured return channel); sandboxed-iframe rendering remains a possible later escape hatch for widgets the catalog can't express.
- Responsive layout utilities and the breakpoint system belong to #119 (mobile shell), not #7 — #7 is primitives-only.
