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

function renderAt(path: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  render(<RouterProvider router={router} />);
}

describe('TopBar', () => {
  it('shows the staticData title of the current route as the page heading', async () => {
    renderAt('/mcp');
    expect(await screen.findByRole('heading', { level: 1, name: 'MCP Servers' })).toBeDefined();
  });

  it('shows the Settings title on /settings', async () => {
    renderAt('/settings');
    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeDefined();
  });

  it('keeps the command-palette slot hidden and inert', async () => {
    renderAt('/sessions');
    await screen.findByRole('heading', { level: 1, name: 'Sessions' });
    const input = document.querySelector<HTMLInputElement>(
      '[data-testid=command-palette-input]'
    );
    expect(input).not.toBeNull();
    expect(input?.disabled).toBe(true);
    expect(input?.closest('[aria-hidden=true]')).not.toBeNull();
  });
});
