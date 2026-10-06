import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { createMemoryHistory, createRouter, RouterProvider } from '@tanstack/react-router';
import { routeTree } from '../../src/router';
import { fetchHealth, fetchAuthStatus, fetchMe } from '../../src/lib/api/client';

// Full-tree renders mount Sidebar -> HealthStatus and the AuthProvider
// probe; keep tests hermetic and default to an authed owner.
const apiMocks = vi.hoisted(() => ({
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      message: string
    ) {
      super(message);
    }
  },
  fetchHealth: vi.fn(),
  fetchAuthStatus: vi.fn(),
  fetchMe: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  bootstrap: vi.fn(),
  setUnauthorizedHandler: vi.fn(),
}));
vi.mock('../../src/lib/api/client', () => apiMocks);
vi.mocked(fetchHealth).mockResolvedValue({ status: 'ok' });
vi.mocked(fetchAuthStatus).mockResolvedValue({ setup_required: false });
vi.mocked(fetchMe).mockResolvedValue({
  id: 'u1',
  username: 'alice',
  display_name: 'Alice',
  role: 'owner',
});

function renderSidebarAt(path: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  // Sidebar renders inside AppShell, which is mounted by the full route tree.
  render(<RouterProvider router={router} />);
}

describe('Sidebar', () => {
  it('renders the brand and all five nav links', async () => {
    renderSidebarAt('/sessions');
    expect(await screen.findByText('Octave')).toBeDefined();
    for (const label of ['Sessions', 'Vault', 'MCP', 'Agents', 'Settings']) {
      expect(screen.getByRole('link', { name: label })).toBeDefined();
    }
  });

  it('marks the current route’s link with aria-current=page', async () => {
    renderSidebarAt('/vault');
    const vault = await screen.findByRole('link', { name: 'Vault' });
    expect(vault.getAttribute('aria-current')).toBe('page');
    const sessions = screen.getByRole('link', { name: 'Sessions' });
    expect(sessions.getAttribute('aria-current')).toBeNull();
  });

  it('links point at the five view routes', async () => {
    renderSidebarAt('/sessions');
    await screen.findByRole('link', { name: 'MCP' });
    expect(screen.getByRole('link', { name: 'Sessions' }).getAttribute('href')).toBe('/sessions');
    expect(screen.getByRole('link', { name: 'Vault' }).getAttribute('href')).toBe('/vault');
    expect(screen.getByRole('link', { name: 'MCP' }).getAttribute('href')).toBe('/mcp');
    expect(screen.getByRole('link', { name: 'Agents' }).getAttribute('href')).toBe('/agents');
    expect(
      screen.getByRole('link', { name: 'Settings' }).getAttribute('href')
    ).toBe('/settings');
  });
});
