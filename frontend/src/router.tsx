import {
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  redirect,
  useRouterState,
} from '@tanstack/react-router';
import AppShell from './components/layout/AppShell';
import RequireAuth from './components/auth/RequireAuth';
import { AuthProvider } from './lib/auth/useAuth';
import PlaceholderView from './views/PlaceholderView';
import LoginView from './views/LoginView';
import SetupView from './views/SetupView';

function NotFoundView() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  return (
    <PlaceholderView
      title="Not Found"
      note={`No view matches ${path}. Use the sidebar to navigate.`}
    />
  );
}

/**
 * Root: mounts AuthProvider so every route (including /login and /setup)
 * can read auth state. App-owned auth lives here, not in main.tsx, so
 * test routers built from routeTree get it too.
 */
function RootView() {
  return (
    <AuthProvider>
      <Outlet />
    </AuthProvider>
  );
}

const rootRoute = createRootRoute({
  component: RootView,
  // Unknown paths render inside the shell (matches previous behavior), but
  // only once RequireAuth has passed. The root's notFoundComponent renders
  // through the root Outlet, so we re-mount the shell around it explicitly.
  notFoundComponent: () => (
    <RequireAuth>
      <AppShell>
        <NotFoundView />
      </AppShell>
    </RequireAuth>
  ),
});

/**
 * Pathless layout: every app view sits behind RequireAuth + the shell.
 * Anonymous -> /login, first-run -> /setup (issue #122).
 */
const protectedRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'protected',
  component: () => (
    <RequireAuth>
      <AppShell />
    </RequireAuth>
  ),
});

/** Pathless layout for the auth screens (rendered without the shell). */
const publicRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'public',
});

const indexRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/sessions' });
  },
});

const sessionsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/sessions',
  staticData: { title: 'Sessions' },
  component: () => <PlaceholderView title="Sessions" note="Chat interface lands in issue #7." />,
});

const vaultRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/vault',
  staticData: { title: 'Vault' },
  component: () => (
    <PlaceholderView
      title="Vault"
      note="Context vault browser lands in the Context Manager UI issues."
    />
  ),
});

const mcpRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/mcp',
  staticData: { title: 'MCP Servers' },
  component: () => (
    <PlaceholderView
      title="MCP Servers"
      note="Server list, config, and tool explorer land in the MCP Connector UI issues."
    />
  ),
});

const agentsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/agents',
  staticData: { title: 'Agents' },
  component: () => (
    <PlaceholderView
      title="Agents"
      note="Agent dashboard and result viewer land in the Agent Manager UI issues."
    />
  ),
});

const settingsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: '/settings',
  staticData: { title: 'Settings' },
  component: () => (
    <PlaceholderView
      title="Settings"
      note="Inference, MCP, and preferences panels land in the Settings UI issues."
    />
  ),
});

const loginRoute = createRoute({
  getParentRoute: () => publicRoute,
  path: '/login',
  component: LoginView,
});

const setupRoute = createRoute({
  getParentRoute: () => publicRoute,
  path: '/setup',
  component: SetupView,
});

export const routeTree = rootRoute.addChildren([
  protectedRoute.addChildren([
    indexRoute,
    sessionsRoute,
    vaultRoute,
    mcpRoute,
    agentsRoute,
    settingsRoute,
  ]),
  publicRoute.addChildren([loginRoute, setupRoute]),
]);

export const router = createRouter({
  routeTree,
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}

declare module '@tanstack/router-core' {
  interface StaticDataRouteOption {
    title?: string;
  }
}
