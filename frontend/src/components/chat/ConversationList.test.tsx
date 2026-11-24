import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConversationList, formatRelativeTime } from './ConversationList';
import type { SessionSummary } from '../../lib/api/sessions';
import type { UseConversations } from '../../lib/api/useConversations';

const NOW = Date.parse('2026-11-24T12:00:00Z');
const session = (over: Partial<SessionSummary> = {}): SessionSummary => ({
  id: 'a',
  title: 'Trip planning',
  created_at: '2026-11-20T00:00:00Z',
  last_activity: new Date(NOW - 5 * 60_000).toISOString(),
  last_message: 'yes, lets go',
  ...over,
});

function renderList(
  over: Partial<UseConversations> = {},
  props: Partial<Parameters<typeof ConversationList>[0]> = {}
) {
  const hooks: UseConversations = {
    sessions: [session()],
    state: 'ready',
    error: null,
    hasMore: false,
    loadingOlder: false,
    refresh: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    loadOlder: vi.fn(),
  };
  render(
    <ConversationList
      conversations={{ ...hooks, ...over }}
      activeId="a"
      onSelect={vi.fn()}
      onNew={vi.fn()}
      onConfirmDelete={vi.fn()}
      {...props}
    />
  );
}

beforeEach(() => vi.clearAllMocks());

describe('formatRelativeTime', () => {
  it('buckets minutes, hours and days', () => {
    expect(formatRelativeTime(new Date(NOW - 30_000).toISOString(), NOW)).toBe('just now');
    expect(formatRelativeTime(new Date(NOW - 5 * 60_000).toISOString(), NOW)).toBe('5m');
    expect(formatRelativeTime(new Date(NOW - 3 * 3_600_000).toISOString(), NOW)).toBe('3h');
    expect(formatRelativeTime(new Date(NOW - 2 * 86_400_000).toISOString(), NOW)).toBe('2d');
  });
});

describe('ConversationList', () => {
  it('renders title, preview and relative time per item', () => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
    try {
      renderList();
      expect(screen.getByRole('button', { name: 'Trip planning' })).toBeDefined();
      expect(screen.getByText('yes, lets go')).toBeDefined();
      expect(screen.getByText('5m')).toBeDefined();
    } finally {
      vi.useRealTimers();
    }
  });

  it('falls back to first line of preview, then to "New conversation"', () => {
    renderList(
      {
        sessions: [
          session({ title: null, last_message: 'buy milk\nmore' }),
          session({ id: 'b', title: null, last_message: null }),
        ],
      },
      { activeId: null }
    );
    expect(screen.getByRole('button', { name: 'buy milk' })).toBeDefined();
    expect(screen.getByRole('button', { name: 'New conversation' })).toBeDefined();
  });

  it('marks the active item with aria-current', () => {
    renderList();
    expect(screen.getByRole('button', { name: 'Trip planning' }).getAttribute('aria-current')).toBe(
      'true'
    );
  });

  it('shows loading and error states', () => {
    renderList({ state: 'loading', sessions: [] });
    expect(screen.getByRole('status')).toBeDefined();
  });

  it('shows the empty state with a New chat affordance', async () => {
    const onNew = vi.fn();
    renderList({ sessions: [] }, { onNew });
    await userEvent.click(screen.getByRole('button', { name: /new chat/i }));
    expect(onNew).toHaveBeenCalled();
  });

  it('delete opens the confirm modal; Cancel keeps it closed, Confirm fires', async () => {
    const onConfirmDelete = vi.fn();
    renderList({}, { onConfirmDelete });
    await userEvent.click(screen.getByRole('button', { name: /delete conversation trip planning/i }));
    expect(await screen.findByText(/this can't be undone/i)).toBeDefined();
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    expect(screen.queryByText(/this can't be undone/i)).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /delete conversation trip planning/i }));
    await userEvent.click(await screen.findByRole('button', { name: /^delete$/i }));
    expect(onConfirmDelete).toHaveBeenCalledWith('a');
  });

  it('shows Load older only when hasMore', async () => {
    renderList({ hasMore: true });
    await userEvent.click(screen.getByRole('button', { name: /load older conversations/i }));
    expect(screen.getByRole('button', { name: /load older conversations/i })).toBeDefined();
  });

  it('surfaces hook errors inline', () => {
    renderList({ error: "Couldn't delete conversation (405)." });
    expect(screen.getByRole('alert').textContent).toContain('405');
  });
});
