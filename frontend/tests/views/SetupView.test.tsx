import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import {
  createMemoryHistory,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router';
import { routeTree } from '../../src/router';
import {
  bootstrap,
  fetchAuthStatus,
  fetchHealth,
  fetchMe,
} from '../../src/lib/api/client';

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

const alice = { id: 'u1', username: 'alice', display_name: 'Alice', role: 'owner' as const };

beforeEach(() => {
  vi.mocked(fetchAuthStatus).mockReset();
  vi.mocked(fetchMe).mockReset();
  vi.mocked(bootstrap).mockReset();
});

describe('SetupView', () => {
  it('renders the first-run form when setup is required', async () => {
    vi.mocked(fetchAuthStatus).mockResolvedValue({ setup_required: true });
    renderAt('/setup');
    expect(await screen.findByRole('heading', { name: 'Welcome to Octave' })).toBeDefined();
    expect(screen.getByLabelText('Username')).toBeDefined();
    expect(screen.getByLabelText('Password')).toBeDefined();
  });

  it('redirects /setup to /login once setup is complete', async () => {
    vi.mocked(fetchAuthStatus).mockResolvedValue({ setup_required: false });
    vi.mocked(fetchMe).mockRejectedValue(new apiMocks.ApiError(401, 'nope'));
    renderAt('/setup');
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeDefined();
  });

  it('creates the owner and lands on Sessions', async () => {
    vi.mocked(fetchAuthStatus).mockResolvedValue({ setup_required: true });
    vi.mocked(bootstrap).mockResolvedValue(alice);
    renderAt('/setup');
    await screen.findByRole('heading', { name: 'Welcome to Octave' });

    fireEvent.change(screen.getByLabelText('Username'), {
      target: { value: 'alice' },
    });
    fireEvent.change(screen.getByLabelText('Display name'), {
      target: { value: 'Alice' },
    });
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'hunter2!' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create owner/i }));

    expect(bootstrap).toHaveBeenCalledWith({
      username: 'alice',
      password: 'hunter2!',
      display_name: 'Alice',
    });
    await waitFor(async () => {
      expect(
        await screen.findByRole('heading', { level: 2, name: 'Sessions' })
      ).toBeDefined();
    });
  });

  it('shows an inline error when a second bootstrap races to 409', async () => {
    vi.mocked(fetchAuthStatus).mockResolvedValue({ setup_required: true });
    vi.mocked(bootstrap).mockRejectedValue(new apiMocks.ApiError(409, 'already'));
    renderAt('/setup');
    await screen.findByRole('heading', { name: 'Welcome to Octave' });

    fireEvent.change(screen.getByLabelText('Username'), {
      target: { value: 'bob' },
    });
    fireEvent.change(screen.getByLabelText('Password'), {
      target: { value: 'pw123456' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create owner/i }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('already been completed');
  });
});
