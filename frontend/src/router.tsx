import {
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
  useRouterState,
} from '@tanstack/react-router';
import AppShell from './components/layout/AppShell';
import PlaceholderView from './views/PlaceholderView';

function NotFoundView() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  return (
    <PlaceholderView
      title="Not Found"
      note={`No view matches ${path}. Use the sidebar to navigate.`}
    />
  );
}

const rootRoute = createRootRoute({
  component: AppShell,
  // Route-level so every router instance built from routeTree (app + tests)
  // renders the custom not-found view.
  notFoundComponent: NotFoundView,
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/sessions' });
  },
});

const sessionsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/sessions',
  staticData: { title: 'Sessions' },
  component: () => <PlaceholderView title="Sessions" note="Chat interface lands in issue #7." />,
});

const vaultRoute = createRoute({
  getParentRoute: () => rootRoute,
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
  getParentRoute: () => rootRoute,
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
  getParentRoute: () => rootRoute,
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
  getParentRoute: () => rootRoute,
  path: '/settings',
  staticData: { title: 'Settings' },
  component: () => (
    <PlaceholderView
      title="Settings"
      note="Inference, MCP, and preferences panels land in the Settings UI issues."
    />
  ),
});

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
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
