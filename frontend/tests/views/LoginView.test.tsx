import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import {
  createMemoryHistory,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router';
import { routeTree } from '../../src/router';
import { fetchHealth, fetchMe, login } from '../../src/lib/api/client';

// Full-tree renders mount Sidebar -> HealthStatus and the AuthProvider probe.
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
vi.mocked(apiMocks.fetchAuthStatus).mockResolvedValue({ setup_required: false });

function renderAt(path: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  render(<RouterProvider router={router} />);
}

const alice = { id: 'u1', username: 'alice', display_name: 'Alice', role: 'owner' as const };

beforeEach(() => {
  vi.mocked(fetchMe).mockReset();
  vi.mocked(login).mockReset();
});

describe('LoginView', () => {
  it('renders username/password fields for anonymous visitors', async () => {
    vi.mocked(fetchMe).mockRejectedValue(new apiMocks.ApiError(401, 'nope'));
    renderAt('/login');
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeDefined();
    expect(screen.getByLabelText('Username')).toBeDefined();
    expect(screen.getByLabelText('Password')).toBeDefined();
  });

  it('shows a field error when login returns 401', async () => {
    vi.mocked(fetchMe).mockRejectedValue(new apiMocks.ApiError(401, 'nope'));
    vi.mocked(login).mockRejectedValue(new apiMocks.ApiError(401, 'Invalid credentials'));
    renderAt('/login');
    await screen.findByRole('heading', { name: 'Sign in' });

    fireEvent.change(screen.getByLabelText('Username'), {
      target: { value: 'alice' },
    });
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'wrong' },
    });
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Invalid username or password');
  });

  it('redirects to /sessions on successful login', async () => {
    vi.mocked(fetchMe).mockRejectedValue(new apiMocks.ApiError(401, 'nope'));
    vi.mocked(login).mockResolvedValue(alice);
    renderAt('/login');
    await screen.findByRole('heading', { name: 'Sign in' });

    fireEvent.change(screen.getByLabelText('Username'), {
      target: { value: 'alice' },
    });
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'pw' },
    });
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }));

    // In-app redirect lands on the Sessions placeholder.
    await waitFor(async () => {
      expect(
        await screen.findByRole('heading', { level: 2, name: 'Sessions' })
      ).toBeDefined();
    });
  });
});
