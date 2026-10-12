import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import {
  createMemoryHistory,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router';
import { routeTree } from '../../src/router';
import { fetchAuthStatus, fetchHealth, fetchMe } from '../../src/lib/api/client';

// RequireAuth renders inside the full route tree (the provider lives at the
// root route), so exercise it through the router the same way the app does.
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

function renderAt(path: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  render(<RouterProvider router={router} />);
}

beforeEach(() => {
  vi.mocked(fetchAuthStatus).mockReset();
  vi.mocked(fetchMe).mockReset();
});

describe('RequireAuth', () => {
  it('renders children for authed users', async () => {
    vi.mocked(fetchAuthStatus).mockResolvedValue({ setup_required: false });
    vi.mocked(fetchMe).mockResolvedValue({
      id: 'u1',
      username: 'alice',
      display_name: 'Alice',
      role: 'owner',
    });
    renderAt('/sessions');
    expect(await screen.findByRole('heading', { level: 2, name: 'Sessions' })).toBeDefined();
  });

  it('redirects anonymous visitors to /login', async () => {
    vi.mocked(fetchAuthStatus).mockResolvedValue({ setup_required: false });
    vi.mocked(fetchMe).mockRejectedValue(new apiMocks.ApiError(401, 'nope'));
    renderAt('/settings');
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeDefined();
  });

  it('redirects first-run instances to /setup', async () => {
    vi.mocked(fetchAuthStatus).mockResolvedValue({ setup_required: true });
    renderAt('/agents');
    expect(await screen.findByRole('heading', { name: 'Welcome to Octave' })).toBeDefined();
  });

  it('protects deep links too (anonymous never sees the shell)', async () => {
    vi.mocked(fetchAuthStatus).mockResolvedValue({ setup_required: false });
    vi.mocked(fetchMe).mockRejectedValue(new apiMocks.ApiError(401, 'nope'));
    renderAt('/vault');
    await screen.findByRole('heading', { name: 'Sign in' });
    expect(screen.queryByRole('complementary')).toBeNull();
  });
});
