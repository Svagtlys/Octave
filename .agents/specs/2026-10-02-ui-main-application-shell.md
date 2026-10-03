# Main Application Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the persistent AppShell (sidebar nav, top bar, content area) with TanStack Router wiring for the five Octave view routes, per spec `.agents/specs/2026-10-02-ui-main-application-shell-design.md`.

**Architecture:** `@tanstack/react-router` with a code-defined route tree; `AppShell` is the root route component rendering sidebar + top bar + `<Outlet/>`; the five views are placeholder components now replaced by issues #7+. Health pill replaces the old `HealthCheck` widget and lives in the sidebar footer.

**Tech Stack:** React 19, TypeScript (strict), Vite 6, Vitest 5 + Testing Library, Playwright, inline styles only (no CSS frameworks — see `.agents/rules/coding.md`).

**Working directory:** all commands run from `frontend/` unless noted. Branch `feature/ui-main-application-shell` is already checked out.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `package.json` | Modify | Add `@tanstack/react-router` runtime dep |
| `src/theme.ts` | Create | Shared color constants (single source of truth for shell colors) |
| `src/views/PlaceholderView.tsx` | Create | Generic "coming soon" card for unwired views |
| `src/router.tsx` | Create | Route tree (6 routes + not-found), router instance, type registration |
| `src/components/layout/AppShell.tsx` | Create | Flex layout: Sidebar + TopBar + `<main><Outlet/></main>` |
| `src/components/layout/Sidebar.tsx` | Create | Brand, nav links with active state, footer with health pill |
| `src/components/layout/TopBar.tsx` | Create | View title `<h1>` from route `staticData`, hidden command-palette slot |
| `src/components/HealthStatus.tsx` | Create | Backend health pill (checking/healthy/unreachable) |
| `src/App.tsx` | Modify | Render `<RouterProvider router={router}/>` |
| `src/components/HealthCheck.tsx` | Delete | Superseded by `HealthStatus.tsx` |
| `tests/App.test.tsx` | Modify | Routing tests (redirect, views, not-found, brand) |
| `tests/views/PlaceholderView.test.tsx` | Create | Placeholder rendering |
| `tests/components/Sidebar.test.tsx` | Create | Nav links + active state |
| `tests/components/TopBar.test.tsx` | Create | Title from staticData |
| `tests/components/HealthStatus.test.tsx` | Create | Health state transitions |
| `tests/e2e/health.spec.ts` | Modify | Old splash-screen assertions are obsolete |
| `tests/e2e/shell.spec.ts` | Create | Click-through navigation + deep links |

---

### Task 1: Install router dependency + theme constants

**Files:**
- Modify: `frontend/package.json` (via npm)
- Create: `frontend/src/theme.ts`

- [ ] **Step 1: Install @tanstack/react-router**

```bash
cd frontend && npm install @tanstack/react-router
```

Expected: exit 0; `"@tanstack/react-router"` appears under `dependencies` in `package.json`.

- [ ] **Step 2: Create theme.ts**

```ts
// src/theme.ts
// Shared color tokens for the application shell.
// Extends the palette in src/index.css; inline styles reference these constants.

export const colors = {
  bg: '#1a1a2e',
  bgDeep: '#14142a',
  panel: '#1f1f38',
  border: '#2e2e4e',
  text: '#eaeaea',
  textDim: '#a0a0b8',
  accent: '#8b7cf6',
  accentSoft: 'rgba(139, 124, 246, 0.14)',
  ok: '#4ade80',
  okSoft: 'rgba(74, 222, 128, 0.12)',
  err: '#f87171',
  errSoft: 'rgba(248, 113, 113, 0.13)',
  warn: '#facc15',
  warnSoft: 'rgba(250, 204, 21, 0.13)',
} as const;
```

- [ ] **Step 3: Verify typecheck + existing tests still pass**

```bash
npx tsc -b && npm run test
```

Expected: tsc silent (exit 0); 2 existing test files pass.

- [ ] **Step 4: Commit**

```bash
git add package.json package-lock.json src/theme.ts
git commit -m "feat(ui): add router dependency and shell theme tokens (#6)"
```

---

### Task 2: PlaceholderView

**Files:**
- Create: `frontend/src/views/PlaceholderView.tsx`
- Test: `frontend/tests/views/PlaceholderView.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/views/PlaceholderView.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import PlaceholderView from "../../src/views/PlaceholderView";

describe("PlaceholderView", () => {
  it("renders the view title as a heading and the note", () => {
    render(<PlaceholderView title="Vault" note="Comes later." />);
    expect(
      screen.getByRole("heading", { level: 2, name: "Vault" })
    ).toBeDefined();
    expect(screen.getByText("Comes later.")).toBeDefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run tests/views/PlaceholderView.test.tsx
```

Expected: FAIL — cannot resolve `../../src/views/PlaceholderView`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// src/views/PlaceholderView.tsx
import type { CSSProperties } from "react";
import { colors } from "../theme";

interface PlaceholderViewProps {
  title: string;
  note: string;
}

/** Dashed card shown for routes whose real UI hasn't been built yet. */
export default function PlaceholderView({ title, note }: PlaceholderViewProps) {
  return (
    <div style={styles.card}>
      <h2 style={styles.heading}>{title}</h2>
      <p style={styles.note}>{note}</p>
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  card: {
    border: `1px dashed ${colors.border}`,
    borderRadius: 9,
    padding: "40px 24px",
    textAlign: "center",
    color: colors.textDim,
    maxWidth: 460,
    margin: "30px auto",
    background: "rgba(255,255,255,0.015)",
  },
  heading: { color: colors.text, fontSize: "1.05rem", marginBottom: 8 },
  note: { fontSize: "0.85rem", lineHeight: 1.5 },
};
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run tests/views/PlaceholderView.test.tsx
```

Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add src/views/PlaceholderView.tsx tests/views/PlaceholderView.test.tsx
git commit -m "feat(ui): add PlaceholderView for unwired routes (#6)"
```

---

### Task 3: Route tree, minimal AppShell, App wiring, delete HealthCheck

**Files:**
- Create: `frontend/src/router.tsx`
- Create: `frontend/src/components/layout/AppShell.tsx`
- Modify: `frontend/src/App.tsx` (full rewrite)
- Delete: `frontend/src/components/HealthCheck.tsx`
- Modify: `frontend/tests/App.test.tsx` (full rewrite)

- [ ] **Step 1: Rewrite App.test.tsx (failing)**

```tsx
// tests/App.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  createMemoryHistory,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { routeTree } from "../src/router";

function renderAt(path: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  render(<RouterProvider router={router} />);
}

describe("App routing", () => {
  it("redirects / to /sessions (shows Sessions view)", async () => {
    renderAt("/");
    expect(
      await screen.findByRole("heading", { level: 2, name: "Sessions" })
    ).toBeDefined();
  });

  it("renders the Vault placeholder at /vault", async () => {
    renderAt("/vault");
    expect(
      await screen.findByRole("heading", { level: 2, name: "Vault" })
    ).toBeDefined();
  });

  it("renders the MCP placeholder at /mcp", async () => {
    renderAt("/mcp");
    expect(
      await screen.findByRole("heading", { level: 2, name: "MCP Servers" })
    ).toBeDefined();
  });

  it("renders the Agents placeholder at /agents", async () => {
    renderAt("/agents");
    expect(
      await screen.findByRole("heading", { level: 2, name: "Agents" })
    ).toBeDefined();
  });

  it("renders the Settings placeholder at /settings", async () => {
    renderAt("/settings");
    expect(
      await screen.findByRole("heading", { level: 2, name: "Settings" })
    ).toBeDefined();
  });

  it("renders a Not Found view for unknown routes", async () => {
    renderAt("/nope");
    expect(
      await screen.findByRole("heading", { level: 2, name: "Not Found" })
    ).toBeDefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run tests/App.test.tsx
```

Expected: FAIL — cannot resolve `../src/router`.

- [ ] **Step 3: Create AppShell (minimal for now; Sidebar/TopBar added in Tasks 4–5)**

```tsx
// src/components/layout/AppShell.tsx
import { Outlet } from "@tanstack/react-router";
import type { CSSProperties } from "react";
import { colors } from "../../theme";

/**
 * Root layout for every view: sidebar + top bar + content area.
 * Route views render inside <main> via <Outlet/>.
 */
export default function AppShell() {
  return (
    <div style={styles.shell}>
      <main style={styles.content}>
        <Outlet />
      </main>
    </div>
  );
}

const styles: Record<string, CSSProperties> = {
  shell: {
    display: "flex",
    minHeight: "100vh",
    background: colors.bg,
  },
  content: {
    flex: 1,
    minWidth: 0,
    overflow: "auto",
    padding: 24,
  },
};
```

- [ ] **Step 4: Create router.tsx**

```tsx
// src/router.tsx
import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
  useRouterState,
} from "@tanstack/react-router";
import AppShell from "./components/layout/AppShell";
import PlaceholderView from "./views/PlaceholderView";

const rootRoute = createRootRoute({
  component: AppShell,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/sessions" });
  },
});

const sessionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/sessions",
  staticData: { title: "Sessions" },
  component: () => (
    <PlaceholderView title="Sessions" note="Chat interface lands in issue #7." />
  ),
});

const vaultRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/vault",
  staticData: { title: "Vault" },
  component: () => (
    <PlaceholderView
      title="Vault"
      note="Context vault browser lands in the Context Manager UI issues."
    />
  ),
});

const mcpRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/mcp",
  staticData: { title: "MCP Servers" },
  component: () => (
    <PlaceholderView
      title="MCP Connector"
      note="Server list, config, and tool explorer land in the MCP Connector UI issues."
    />
  ),
});

const agentsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/agents",
  staticData: { title: "Agents" },
  component: () => (
    <PlaceholderView
      title="Agents"
      note="Agent dashboard and result viewer land in the Agent Manager UI issues."
    />
  ),
});

const settingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/settings",
  staticData: { title: "Settings" },
  component: () => (
    <PlaceholderView
      title="Settings"
      note="Inference, MCP, and preferences panels land in the Settings UI issues."
    />
  ),
});

function NotFoundView() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  return (
    <PlaceholderView
      title="Not Found"
      note={`No view matches ${path}. Use the sidebar to navigate.`}
    />
  );
}

export const routeTree = rootRoute.addChildren([
  indexRoute,
  sessionsRoute,
  vaultRoute,
  mcpRoute,
  agentsRoute,
  settingsRoute,
]);

export const router = createRouter({
  routeTree,
  defaultNotFoundComponent: NotFoundView,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
```

- [ ] **Step 5: Rewrite App.tsx**

```tsx
// src/App.tsx
import { RouterProvider } from "@tanstack/react-router";
import { router } from "./router";

export default function App() {
  return <RouterProvider router={router} />;
}
```

- [ ] **Step 6: Delete HealthCheck.tsx**

```bash
rm src/components/HealthCheck.tsx
```

(`App.tsx` no longer imports it; `HealthStatus.tsx` arrives in Task 6.)

- [ ] **Step 7: Run tests to verify they pass**

```bash
npx vitest run tests/App.test.tsx
```

Expected: PASS (6 tests).

- [ ] **Step 8: Commit**

```bash
git add src/router.tsx src/components/layout/AppShell.tsx src/App.tsx tests/App.test.tsx
git rm src/components/HealthCheck.tsx
git commit -m "feat(ui): add route tree and minimal AppShell, wire App to router (#6)"
```

---

### Task 4: Sidebar

**Files:**
- Create: `frontend/src/components/layout/Sidebar.tsx`
- Modify: `frontend/src/components/layout/AppShell.tsx` (mount Sidebar)
- Modify: `frontend/tests/App.test.tsx` (add brand + aside assertions)
- Test: `frontend/tests/components/Sidebar.test.tsx`

- [ ] **Step 1: Write the failing Sidebar test**

```tsx
// tests/components/Sidebar.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  createMemoryHistory,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { routeTree } from "../../src/router";
import Sidebar from "../../src/components/layout/Sidebar";

function renderSidebarAt(path: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  render(
    <RouterProvider
      router={router}
      defaultPreload="off"
      // Sidebar needs router context only; render it directly.
      InnerWrap={() => <Sidebar />}
    />
  );
}

describe("Sidebar", () => {
  it("renders the brand and all five nav links", () => {
    renderSidebarAt("/sessions");
    expect(screen.getByText("Octave")).toBeDefined();
    for (const label of ["Sessions", "Vault", "MCP", "Agents", "Settings"]) {
      expect(screen.getByRole("link", { name: label })).toBeDefined();
    }
  });

  it("marks the current route's link with aria-current=page", async () => {
    renderSidebarAt("/vault");
    const vault = await screen.findByRole("link", { name: "Vault" });
    expect(vault.getAttribute("aria-current")).toBe("page");
    const sessions = screen.getByRole("link", { name: "Sessions" });
    expect(sessions.getAttribute("aria-current")).toBeNull();
  });

  it("links point at the five view routes", async () => {
    renderSidebarAt("/sessions");
    await screen.findByRole("link", { name: "MCP" });
    expect(screen.getByRole("link", { name: "Sessions" }).getAttribute("href")).toBe("/sessions");
    expect(screen.getByRole("link", { name: "Vault" }).getAttribute("href")).toBe("/vault");
    expect(screen.getByRole("link", { name: "MCP" }).getAttribute("href")).toBe("/mcp");
    expect(screen.getByRole("link", { name: "Agents" }).getAttribute("href")).toBe("/agents");
    expect(screen.getByRole("link", { name: "Settings" }).getAttribute("href")).toBe("/settings");
  });
});
```

Note: `InnerWrap` is not a RouterProvider prop in current v1. If tsc rejects it, instead export the Sidebar inside a real route by rendering the full tree:

```tsx
function renderSidebarAt(path: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  render(<RouterProvider router={router} />);
}
```

and rely on `AppShell` mounting `Sidebar` (Step 5) — the same assertions apply because Sidebar renders inside the shell. Use this simpler variant; drop `InnerWrap`.

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run tests/components/Sidebar.test.tsx
```

Expected: FAIL — cannot resolve `../../src/components/layout/Sidebar`.

- [ ] **Step 3: Create Sidebar.tsx**

```tsx
// src/components/layout/Sidebar.tsx
import { Link } from "@tanstack/react-router";
import type { CSSProperties } from "react";
import { colors } from "../../theme";

const NAV_ITEMS = [
  { to: "/sessions", label: "Sessions", icon: "☰" },
  { to: "/vault", label: "Vault", icon: "▤" },
  { to: "/mcp", label: "MCP", icon: "⛓" },
  { to: "/agents", label: "Agents", icon: "◎" },
  { to: "/settings", label: "Settings", icon: "⚙" },
] as const;

/** Fixed-width navigation sidebar: brand, view links, health footer. */
export default function Sidebar() {
  return (
    <aside style={styles.sidebar}>
      <div style={styles.brand}>
        <span style={styles.brandMark}>◐</span>
        <span>Octave</span>
      </div>
      <nav style={styles.nav}>
        {NAV_ITEMS.map((item) => (
          <Link
            key={item.to}
            to={item.to}
            style={styles.navLink}
            activeProps={{
              style: styles.navLinkActive,
              "aria-current": "page",
            }}
          >
            <span style={styles.navIcon}>{item.icon}</span>
            {item.label}
          </Link>
        ))}
      </nav>
      <div style={styles.footer} />
    </aside>
  );
}

const styles: Record<string, CSSProperties> = {
  sidebar: {
    width: 210,
    flexShrink: 0,
    background: colors.bgDeep,
    borderRight: `1px solid ${colors.border}`,
    display: "flex",
    flexDirection: "column",
    padding: "16px 10px",
  },
  brand: {
    display: "flex",
    alignItems: "center",
    gap: 9,
    padding: "4px 10px 18px",
    fontWeight: 700,
    fontSize: "1.05rem",
    letterSpacing: "0.02em",
  },
  brandMark: {
    width: 26,
    height: 26,
    borderRadius: 7,
    background: `linear-gradient(135deg, ${colors.accent}, #5a4fcf)`,
    display: "grid",
    placeItems: "center",
    fontSize: 15,
    color: "#fff",
    fontWeight: 800,
  },
  nav: { display: "flex", flexDirection: "column", gap: 2, flex: 1 },
  navLink: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "9px 12px",
    borderRadius: 7,
    color: colors.textDim,
    fontSize: "0.9rem",
    textDecoration: "none",
    borderLeft: `3px solid transparent`,
  },
  navLinkActive: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "9px 12px",
    borderRadius: 7,
    color: colors.text,
    fontSize: "0.9rem",
    fontWeight: 600,
    textDecoration: "none",
    background: colors.accentSoft,
    borderLeft: `3px solid ${colors.accent}`,
  },
  navIcon: { width: 16, textAlign: "center", opacity: 0.85 },
  footer: {
    borderTop: `1px solid ${colors.border}`,
    padding: "12px 10px 2px",
    display: "flex",
    alignItems: "center",
    gap: 8,
  },
};
```

- [ ] **Step 4: Mount Sidebar in AppShell**

Replace the `AppShell` component body in `src/components/layout/AppShell.tsx`:

```tsx
export default function AppShell() {
  return (
    <div style={styles.shell}>
      <Sidebar />
      <main style={styles.content}>
        <Outlet />
      </main>
    </div>
  );
}
```

and add the import at the top of the file:

```tsx
import Sidebar from "./Sidebar";
```

- [ ] **Step 5: Add shell-level assertions to App.test.tsx**

Append to the `describe("App routing", ...)` block in `tests/App.test.tsx`:

```tsx
  it("shows the brand and sidebar on every route", async () => {
    renderAt("/agents");
    expect(await screen.findByText("Octave")).toBeDefined();
    expect(screen.getByRole("complementary")).toBeDefined();
  });
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
npx vitest run tests/components/Sidebar.test.tsx tests/App.test.tsx
```

Expected: PASS (Sidebar 3, App 7).

- [ ] **Step 7: Commit**

```bash
git add src/components/layout/Sidebar.tsx src/components/layout/AppShell.tsx tests/components/Sidebar.test.tsx tests/App.test.tsx
git commit -m "feat(ui): add navigation sidebar with active-state links (#6)"
```

---

### Task 5: TopBar

**Files:**
- Create: `frontend/src/components/layout/TopBar.tsx`
- Modify: `frontend/src/components/layout/AppShell.tsx` (mount TopBar above content)
- Modify: `frontend/tests/App.test.tsx` (top-bar title assertions)
- Test: `frontend/tests/components/TopBar.test.tsx`

- [ ] **Step 1: Write the failing TopBar test**

```tsx
// tests/components/TopBar.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  createMemoryHistory,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router";
import { routeTree } from "../../src/router";

function renderAt(path: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  render(<RouterProvider router={router} />);
}

describe("TopBar", () => {
  it("shows the staticData title of the current route as the page heading", async () => {
    renderAt("/mcp");
    expect(
      await screen.findByRole("heading", { level: 1, name: "MCP Servers" })
    ).toBeDefined();
  });

  it("shows the Settings title on /settings", async () => {
    renderAt("/settings");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Settings" })
    ).toBeDefined();
  });

  it("keeps the command-palette slot hidden and inert", async () => {
    renderAt("/sessions");
    await screen.findByRole("heading", { level: 1, name: "Sessions" });
    const input = document.querySelector<HTMLInputElement>(
      "[data-testid=command-palette-input]"
    );
    expect(input).not.toBeNull();
    expect(input?.disabled).toBe(true);
    expect(input?.closest("[aria-hidden=true]")).not.toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run tests/components/TopBar.test.tsx
```

Expected: FAIL — heading level 1 not found (TopBar doesn't exist yet).

- [ ] **Step 3: Create TopBar.tsx**

```tsx
// src/components/layout/TopBar.tsx
import { useRouterState } from "@tanstack/react-router";
import type { CSSProperties } from "react";
import { colors } from "../../theme";

/**
 * Top bar: view title (from the matched route's staticData.title) plus a
 * reserved command-palette slot that ships hidden/disabled until a future
 * issue enables it.
 */
export default function TopBar() {
  const title = useRouterState({
    select: (s) => {
      let found: string | undefined;
      for (const match of s.matches) {
        const staticTitle = match.staticData?.title;
        if (typeof staticTitle === "string") found = staticTitle;
      }
      return found;
    },
  });

  return (
    <header style={styles.topbar}>
      <h1 style={styles.title}>{title ?? "Octave"}</h1>
      <div style={styles.spacer} />
      <div aria-hidden="true" style={styles.paletteSlot}>
        <input
          data-testid="command-palette-input"
          disabled
          placeholder="Search or run a command… (Ctrl+K)"
          style={styles.paletteInput}
        />
      </div>
    </header>
  );
}

const styles: Record<string, CSSProperties> = {
  topbar: {
    height: 52,
    flexShrink: 0,
    borderBottom: `1px solid ${colors.border}`,
    display: "flex",
    alignItems: "center",
    padding: "0 20px",
    gap: 14,
    background: colors.panel,
  },
  title: { fontSize: "1rem", fontWeight: 600 },
  spacer: { flex: 1 },
  paletteSlot: { display: "none" },
  paletteInput: {
    fontSize: "0.8rem",
    color: colors.textDim,
    background: colors.bgDeep,
    border: `1px solid ${colors.border}`,
    borderRadius: 7,
    padding: "6px 12px",
    width: 200,
  },
};
```

- [ ] **Step 4: Mount TopBar in AppShell**

Replace the `AppShell` body in `src/components/layout/AppShell.tsx`:

```tsx
export default function AppShell() {
  return (
    <div style={styles.shell}>
      <Sidebar />
      <div style={styles.mainColumn}>
        <TopBar />
        <main style={styles.content}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
```

Add the import at the top of the file:

```tsx
import TopBar from "./TopBar";
```

Add to the `styles` object in the same file:

```tsx
  mainColumn: {
    flex: 1,
    minWidth: 0,
    display: "flex",
    flexDirection: "column",
  },
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
npx vitest run tests/components/TopBar.test.tsx tests/App.test.tsx tests/components/Sidebar.test.tsx
```

Expected: PASS (TopBar 3, App 7, Sidebar 3).

- [ ] **Step 6: Commit**

```bash
git add src/components/layout/TopBar.tsx src/components/layout/AppShell.tsx tests/components/TopBar.test.tsx
git commit -m "feat(ui): add top bar with view title and hidden command-palette slot (#6)"
```

---

### Task 6: HealthStatus pill (replaces HealthCheck)

**Files:**
- Create: `frontend/src/components/HealthStatus.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx` (render pill in footer)
- Test: `frontend/tests/components/HealthStatus.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// tests/components/HealthStatus.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import HealthStatus from "../../src/components/HealthStatus";
import { fetchHealth } from "../../src/lib/api/client";

vi.mock("../../src/lib/api/client", () => ({
  fetchHealth: vi.fn(),
}));

describe("HealthStatus", () => {
  beforeEach(() => {
    vi.mocked(fetchHealth).mockReset();
  });

  it("shows Healthy after a successful health check", async () => {
    vi.mocked(fetchHealth).mockResolvedValue({ status: "ok" });
    render(<HealthStatus />);
    expect(screen.getByText(/checking/i)).toBeDefined();
    expect(await screen.findByText("Healthy")).toBeDefined();
  });

  it("shows Unreachable when the backend is down", async () => {
    vi.mocked(fetchHealth).mockRejectedValue(new Error("network"));
    render(<HealthStatus />);
    expect(await screen.findByText("Unreachable")).toBeDefined();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

```bash
npx vitest run tests/components/HealthStatus.test.tsx
```

Expected: FAIL — cannot resolve `../../src/components/HealthStatus`.

- [ ] **Step 3: Create HealthStatus.tsx**

```tsx
// src/components/HealthStatus.tsx
import { useEffect, useState } from "react";
import type { CSSProperties } from "react";
import { fetchHealth } from "../lib/api/client";
import { colors } from "../theme";

type HealthState = "checking" | "healthy" | "unreachable";

const PILL: Record<HealthState, { label: string; fg: string; bg: string; border: string }> = {
  checking: { label: "Checking…", fg: colors.warn, bg: colors.warnSoft, border: colors.warn },
  healthy: { label: "Healthy", fg: colors.ok, bg: colors.okSoft, border: colors.ok },
  unreachable: { label: "Unreachable", fg: colors.err, bg: colors.errSoft, border: colors.err },
};

/**
 * Compact backend-health pill for the sidebar footer.
 * Supersedes the old HealthCheck widget; routes through fetchHealth()
 * (the apiFetch client) instead of raw fetch.
 */
export default function HealthStatus() {
  const [state, setState] = useState<HealthState>("checking");

  useEffect(() => {
    let cancelled = false;
    fetchHealth()
      .then(() => {
        if (!cancelled) setState("healthy");
      })
      .catch(() => {
        if (!cancelled) setState("unreachable");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const pill = PILL[state];
  return (
    <span
      data-testid="health-pill"
      style={{ ...styles.pill, color: pill.fg, background: pill.bg, borderColor: pill.border }}
    >
      <span style={{ ...styles.dot, background: pill.fg }} />
      {pill.label}
    </span>
  );
}

const styles: Record<string, CSSProperties> = {
  pill: {
    display: "inline-flex",
    alignItems: "center",
    gap: 7,
    fontSize: "0.74rem",
    fontWeight: 600,
    padding: "5px 11px",
    borderRadius: 999,
    border: "1px solid",
    whiteSpace: "nowrap",
  },
  dot: { width: 7, height: 7, borderRadius: "50%" },
};
```

- [ ] **Step 4: Run test to verify it passes**

```bash
npx vitest run tests/components/HealthStatus.test.tsx
```

Expected: PASS (2 tests).

- [ ] **Step 5: Render the pill in the Sidebar footer**

In `src/components/layout/Sidebar.tsx`, replace the empty footer div:

```tsx
      <div style={styles.footer} />
```

with:

```tsx
      <div style={styles.footer}>
        <HealthStatus />
      </div>
```

and add the import at the top of the file:

```tsx
import HealthStatus from "../HealthStatus";
```

- [ ] **Step 6: Run the full unit suite**

```bash
npm run test
```

Expected: all test files pass.

- [ ] **Step 7: Commit**

```bash
git add src/components/HealthStatus.tsx src/components/layout/Sidebar.tsx tests/components/HealthStatus.test.tsx
git commit -m "feat(ui): add backend health pill to sidebar footer (#6)"
```

---

### Task 7: E2E tests

**Files:**
- Modify: `frontend/tests/e2e/health.spec.ts` (full rewrite — old splash assertion is obsolete)
- Create: `frontend/tests/e2e/shell.spec.ts`

- [ ] **Step 1: Rewrite health.spec.ts**

```ts
// tests/e2e/health.spec.ts
import { test, expect } from "@playwright/test";

test("app loads and redirects to /sessions", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Octave")).toBeVisible();
  await expect(page).toHaveURL(/\/sessions$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "Sessions" })
  ).toBeVisible();
});
```

- [ ] **Step 2: Create shell.spec.ts**

```ts
// tests/e2e/shell.spec.ts
import { test, expect } from "@playwright/test";

test("sidebar navigation switches views", async ({ page }) => {
  await page.goto("/sessions");
  await page.getByRole("link", { name: "Vault" }).click();
  await expect(page).toHaveURL(/\/vault$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "Vault" })
  ).toBeVisible();

  await page.getByRole("link", { name: "MCP" }).click();
  await expect(page).toHaveURL(/\/mcp$/);
  await expect(
    page.getByRole("heading", { level: 1, name: "MCP Servers" })
  ).toBeVisible();
});

test("deep link to /settings renders the Settings view", async ({ page }) => {
  await page.goto("/settings");
  await expect(
    page.getByRole("heading", { level: 1, name: "Settings" })
  ).toBeVisible();
});

test("unknown route renders Not Found inside the shell", async ({ page }) => {
  await page.goto("/does-not-exist");
  await expect(
    page.getByRole("heading", { level: 2, name: "Not Found" })
  ).toBeVisible();
  await expect(page.getByText("Octave")).toBeVisible();
});

test("health pill renders in one of its three states", async ({ page }) => {
  await page.goto("/sessions");
  const pill = page.getByTestId("health-pill");
  await expect(pill).toBeVisible();
  await expect(pill).toContainText(/Healthy|Unreachable|Checking/);
});
```

- [ ] **Step 3: Run e2e against the Vite dev server**

```bash
npm run test:e2e
```

Expected: all specs pass (Playwright auto-starts the dev server via `webServer` in `playwright.config.ts`). The health pill will read "Unreachable" when the backend proxy target (`http://backend:8000`) isn't running — the spec accepts any of the three states.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/health.spec.ts tests/e2e/shell.spec.ts
git commit -m "test(e2e): cover app shell navigation, deep links, and health pill (#6)"
```

---

### Task 8: Full verification

- [ ] **Step 1: Typecheck + build**

```bash
npx tsc -b && npm run build
```

Expected: exit 0, no type errors.

- [ ] **Step 2: Lint + format check**

```bash
npm run lint && npm run format:check
```

Expected: exit 0. If format fails, run `npm run format` and re-check.

- [ ] **Step 3: Full unit + e2e run**

```bash
npm run test && npm run test:e2e
```

Expected: all pass.

- [ ] **Step 4: Manual visual check**

```bash
npm run dev
```

Open `http://localhost:5173`, compare against `plans/mockups/appshell-v2.html`: sidebar (brand, 5 links, active accent), top bar title changes per route, health pill bottom-left, `/` redirects to `/sessions`.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A && git commit -m "chore(ui): fixups from shell verification (#6)" || echo "nothing to commit"
```

- [ ] **Step 6: Push to the draft PR branch**

```bash
git push origin feature/ui-main-application-shell
```

Expected: push succeeds; PR https://github.com/Svagtlys/Octave/pull/118 updates with the commits.

---

## Self-Review Notes

- **Spec coverage:** route table (Task 3), sidebar + active state (Task 4), top bar + hidden palette slot (Task 5), health pill + `fetchHealth()` rule compliance (Task 6), not-found inside shell (Task 3 + e2e), tests per spec §Testing (Tasks 3–7), deletion of `HealthCheck.tsx` (Task 3). No gaps.
- **Known API risks (flag for executing agent):** TanStack Router v1 minor releases shuffle a few signatures. If `useRouterState({ select })`, `activeProps`, `staticData` on matches, or `defaultNotFoundComponent` behave differently on the installed version, consult the installed package's type defs and adapt — do not switch router libraries. The Sidebar test's `InnerWrap` note in Task 4 Step 1 documents the fallback (render the full tree) up front.
- **Type consistency:** `routeTree` exported from `src/router.tsx` used by all tests; `colors` keys used by Sidebar/TopBar/HealthStatus all defined in Task 1's `theme.ts`; `data-testid` names `health-pill` / `command-palette-input` consistent across component and specs.
