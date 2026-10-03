# Main Application Shell — Design

**Issue:** [#6 feat(ui): build main application shell](https://github.com/Svagtlys/Octave/issues/6)
**PR:** https://github.com/Svagtlys/Octave/pull/118
**Branch:** `feature/ui-main-application-shell`
**Date:** 2026-10-02
**Visual reference:** `plans/mockups/appshell-v2.html` (approved mockup)

## Goal

Build the persistent application shell — sidebar navigation, top bar, and content area — that every future UI view (sessions/chat #7, vault, MCP connector, agent manager, settings) renders inside. Establish the routing convention (`@tanstack/react-router`, code-defined route tree) used by the rest of the UI track.

## Scope

**In scope**

- `AppShell` layout component (sidebar + top bar + content area)
- Route tree with six routes; `/` redirects to `/sessions`
- Sidebar nav with active-state highlighting, wired to all five view routes
- Top bar showing the current view title (`<h1>`) and a hidden/disabled command-palette slot
- Health status pill (replaces `HealthCheck` widget) in the sidebar footer
- Placeholder views for all five routes + not-found view inside the shell
- Unit + e2e tests for shell and routing

**Out of scope**

- Real view content (issues #7+ fill the placeholders)
- Responsive sidebar collapse / mobile behavior (UI Core Layout #2)
- Shared UI component library (UI Core Layout #2)
- Command palette implementation (slot only)

## Approved visual design (mockup v2)

- **Sidebar (fixed 210 px, `#14142a`):** brand mark + "Octave" at top; five nav items — Sessions, Vault, MCP, Agents, Settings; footer with health pill above a top border.
- **Active nav item:** accent left border (`#8b7cf6`), tinted background (`rgba(139,124,246,0.14)`), full-contrast text. Inactive: dim text (`#a0a0b8`).
- **Top bar (52 px, `#1f1f38`):** view title only, left-aligned. Right side reserved for a command-palette slot that ships `display: none` (present in component tree, invisible to users and screen readers until a future issue enables it).
- **Health pill (sidebar footer):** rounded pill, green tint + dot when healthy, red when unreachable, yellow while checking. Compact label ("Healthy" / "Unreachable" / "Checking…").
- **Content area:** route views render here; placeholders are dashed-border cards centered in the content area with the view name and a one-line note.
- **Palette:** extends existing `index.css` (`--bg #1a1a2e`, `--text #eaeaea`, accent `#8b7cf6`). Colors centralized in `src/theme.ts`.

## Routing decision

**Chosen: `@tanstack/react-router`** (user-approved). Rationale:

1. Ecosystem consistency with the TanStack Query mandate in `.agents/rules/coding.md`; route `loader`s prefetch into the Query cache for future views.
2. Typed routes end-to-end — satisfies the strict-TS / no-`any` rule.
3. Native layout nesting: `AppShell` is the root route's component; views are child routes rendering through `<Outlet/>`. Active-link state (`activeProps`, `aria-current`) is built in — no hand-rolled path matching.

Alternatives considered and rejected: `react-router-dom` (untyped params, no loader→Query seam) and a bespoke ~60-line hash router (re-implements pushstate/popstate/encoding/active-matching, guaranteed migration later).

**Configuration:** code-defined route tree in `src/router.tsx` (no file-based routing, no Vite plugin, no codegen).

## Route table

| Path       | Title (top bar)    | Content                                   |
|------------|--------------------|-------------------------------------------|
| `/`        | —                  | `redirect` → `/sessions` (in `beforeLoad`)|
| `/sessions`| `Sessions`         | Placeholder — real chat UI in issue #7    |
| `/vault`   | `Vault`            | Placeholder — Context Manager views       |
| `/mcp`     | `MCP Servers`      | Placeholder — MCP Connector views         |
| `/agents`  | `Agents`           | Placeholder — Agent Manager views         |
| `/settings`| `Settings`         | Placeholder — Settings panels             |
| `*`        | `Not Found`        | Not-found view inside the shell           |

Titles live in each route's `staticData: { title: string }`; the `TopBar` derives the current title from `useRouterState` over the matched route chain. Unknown routes render the not-found component **inside** the shell (sidebar stays visible).

## Component architecture

```
src/
  main.tsx                  # unchanged (renders <App/>)
  App.tsx                   # rewritten: RouterProvider wiring
  router.tsx                # NEW: root route + 6 child routes + router instance
  theme.ts                  # NEW: shared color constants
  components/
    layout/
      AppShell.tsx          # NEW: flex layout; sidebar + top bar + <Outlet/>
      Sidebar.tsx           # NEW: brand, nav links (active state), footer slot
      TopBar.tsx            # NEW: view title h1 + hidden command-palette slot
    HealthStatus.tsx        # NEW (replaces HealthCheck.tsx): pill widget
  views/
    PlaceholderView.tsx     # NEW: generic coming-soon card (title, note)
  components/HealthCheck.tsx # DELETED (superseded by HealthStatus.tsx)
```

Responsibilities and interfaces:

- **`AppShell`** — pure layout, no props; renders `Sidebar`, `TopBar`, and `<main>{<Outlet/>}</main>`. Only mounted by the root route.
- **`Sidebar`** — nav config is a local array `{ to, label, icon }`; renders `<Link>` per item with `activeProps`/`inactiveProps` styles and `aria-current="page"` when active. Footer renders `<HealthStatus/>`.
- **`TopBar`** — reads title via `useRouterState(s => s.matches.findLast(m => m.route.staticData?.title)?.route.staticData?.title)`. The command-palette stub is `<div aria-hidden style={{display:'none'}}><input disabled …/></div>`.
- **`HealthStatus`** — calls `fetchHealth()` from `src/lib/api/client.ts` on mount (fixes the raw-`fetch` rule violation in the old HealthCheck); three states (checking/healthy/unreachable) → pill color + label. Handles loading and error states per coding rules.
- **`PlaceholderView`** — props `{ title: string; note: string }`; dashed card. Not-found view is a `PlaceholderView` variant ("Not Found", note includes the attempted path).

Styling: inline `React.CSSProperties` objects only (`.agents/rules/coding.md` forbids CSS frameworks/preprocessors). Theme colors exported from `theme.ts` and referenced by all shell components.

## Data flow & error handling

- No server state beyond the health poll. `HealthStatus` owns its fetch lifecycle; on unmount the result is discarded (no global store yet — TanStack Query wiring arrives with the first data-driven view).
- Health fetch failure (backend down) → red pill "Unreachable" — never a blank/error surface in the shell.
- Route mismatch → not-found view inside the shell (sidebar and top bar intact), so users can always navigate back.
- Vite dev server SPA fallback handles deep links (`/vault` typed directly). Dockerfile runs the dev server, so this covers both dev and containerized e2e.

## Testing strategy

**Unit (Vitest + Testing Library, jsdom):**

- `Sidebar.test.tsx` — renders the five nav links with correct `href`s; active link carries `aria-current="page"` per the router's current location (memory history).
- `HealthStatus.test.tsx` — mocks `fetchHealth`; asserts checking → healthy and checking → unreachable transitions.
- `App.test.tsx` (rewritten) — memory-history router: `/` resolves to Sessions (heading + active nav); each of the five routes renders its placeholder title; unknown path renders "Not Found" **and** still shows the sidebar; brand "Octave" visible on all routes.

**E2E (Playwright):**

- `health.spec.ts` (updated) — the old assertion (`heading /octave/i`) breaks because the brand is no longer a heading and the centered layout is gone. Updated: loads `/`, asserts brand text visible and redirects to `/sessions` (heading "Sessions").
- `shell.spec.ts` (new) — click-through: from `/sessions`, click each nav item, assert URL and top-bar title change; deep-link directly to `/settings`. Health pill asserts presence of one of the three states (backend availability is not guaranteed in the e2e webServer config).

**Dependency:** `npm install @tanstack/react-router` (v1, React 19 compatible; runtime dep).

## Consequences for existing code

- `App.tsx` loses its centered splash layout; the `h1` becomes the per-view top-bar title. The "Octave" brand remains visible (sidebar). Both existing tests (`App.test.tsx`, `tests/e2e/health.spec.ts`) are updated in this work item accordingly.
- `HealthCheck.tsx` is deleted; `HealthStatus.tsx` replaces it and routes through `apiFetch`-layer `fetchHealth()`.

## Success criteria

1. `npm run test` and `npm run lint` pass in `frontend/`.
2. Shell renders on all six routes; sidebar active state tracks the URL; `/` redirects to `/sessions`; unknown routes stay in-shell.
3. Playwright `test:e2e` passes against the Vite dev server.
4. No CSS files added; no `any` types; health widget degrades to a visible "Unreachable" pill when the backend is down.
