import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { createMemoryHistory, createRouter, RouterProvider } from '@tanstack/react-router';
import { routeTree } from '../src/router';

// SessionChatView needs a live-ish transcript (empty page is fine) and the
// panel needs the session list. The socket is stubbed open.
const apiMocks = vi.hoisted(() => ({
  fetchHealth: vi.fn(),
  fetchAuthStatus: vi.fn(),
  fetchMe: vi.fn(),
}));
vi.mock('../src/lib/api/client', () => ({
  ...apiMocks,
  ApiError: class ApiError extends Error {
    constructor(
      public status: number,
      message: string
    ) {
      super(message);
    }
  },
  setUnauthorizedHandler: vi.fn(),
}));
const sessionsMocks = vi.hoisted(() => ({
  fetchSessions: vi.fn(),
  fetchLatestMessages: vi.fn(),
  fetchOlderMessages: vi.fn(),
  createSession: vi.fn(),
  deleteSession: vi.fn(),
}));
vi.mock('../src/lib/api/sessions', () => sessionsMocks);
vi.mock('../src/lib/api/useSessionSocket', () => ({
  useSessionSocket: () => ({ status: 'open', sendUserMessage: vi.fn() }),
}));

const row = (id: string) => ({
  id,
  title: `Chat ${id}`,
  created_at: '2026-11-20T00:00:00Z',
  last_activity: '2026-11-24T12:00:00Z',
  last_message: 'preview',
});

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.fetchHealth.mockResolvedValue({ status: 'ok' });
  apiMocks.fetchAuthStatus.mockResolvedValue({ setup_required: false });
  apiMocks.fetchMe.mockResolvedValue({
    id: 'u1',
    username: 'alice',
    display_name: 'Alice',
    role: 'owner',
  });
  sessionsMocks.fetchLatestMessages.mockResolvedValue({
    items: [],
    next_cursor: null,
    prev_cursor: null,
  });
  sessionsMocks.fetchSessions.mockResolvedValue({
    items: [row('b'), row('a')],
    total: 2,
    limit: 50,
    offset: 0,
  });
});

function renderAt(path: string) {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  render(<RouterProvider router={router} />);
  return router;
}

describe('conversation history in the chat view', () => {
  it('mounts the panel with the active session highlighted', async () => {
    renderAt('/sessions/a');
    const item = await screen.findByRole('button', { name: 'Chat a' });
    expect(item.getAttribute('aria-current')).toBe('true');
  });

  it('switching sessions re-fetches the transcript and updates the URL', async () => {
    renderAt('/sessions/a');
    await userEvent.click(await screen.findByRole('button', { name: 'Chat b' }));
    await waitFor(() => expect(sessionsMocks.fetchLatestMessages).toHaveBeenCalledWith('b'));
    expect(screen.getByRole('button', { name: 'Chat b' }).getAttribute('aria-current')).toBe('true');
  });

  it('New chat creates a session and navigates into it', async () => {
    sessionsMocks.createSession.mockResolvedValue(row('new'));
    renderAt('/sessions/a');
    await userEvent.click(await screen.findByRole('button', { name: /new chat/i }));
    await waitFor(() => expect(sessionsMocks.createSession).toHaveBeenCalled());
    await waitFor(() => expect(sessionsMocks.fetchLatestMessages).toHaveBeenCalledWith('new'));
  });

  it('deleting the active session navigates to the next most recent', async () => {
    sessionsMocks.deleteSession.mockResolvedValue(undefined);
    renderAt('/sessions/a');
    await userEvent.click(
      await screen.findByRole('button', { name: /delete conversation chat a/i })
    );
    await userEvent.click(await screen.findByRole('button', { name: /^delete$/i }));
    await waitFor(() => expect(sessionsMocks.deleteSession).toHaveBeenCalledWith('a'));
    await waitFor(() => expect(sessionsMocks.fetchLatestMessages).toHaveBeenCalledWith('b'));
  });

  it('deleting the last session lands on the hub', async () => {
    sessionsMocks.fetchSessions.mockResolvedValue({
      items: [row('a')],
      total: 1,
      limit: 50,
      offset: 0,
    });
    sessionsMocks.deleteSession.mockResolvedValue(undefined);
    renderAt('/sessions/a');
    await userEvent.click(
      await screen.findByRole('button', { name: /delete conversation chat a/i })
    );
    await userEvent.click(await screen.findByRole('button', { name: /^delete$/i }));
    expect(await screen.findByRole('heading', { level: 2, name: 'Sessions' })).toBeDefined();
  });

  it('failed delete (stub endpoint) surfaces an inline error and keeps the session', async () => {
    sessionsMocks.deleteSession.mockRejectedValue({
      status: 405,
      name: 'ApiError',
      message: '',
    });
    renderAt('/sessions/a');
    await userEvent.click(
      await screen.findByRole('button', { name: /delete conversation chat a/i })
    );
    await userEvent.click(await screen.findByRole('button', { name: /^delete$/i }));
    const alertEl = await screen.findByRole('alert');
    expect(alertEl.textContent).toMatch(/delete/i);
    expect(screen.getByRole('button', { name: 'Chat a' })).toBeDefined();
  });
});
