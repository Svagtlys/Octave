import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import SessionChatView from '../../src/views/SessionChatView';
import { fetchLatestMessages, fetchOlderMessages } from '../../src/lib/api/sessions';
import { ApiError } from '../../src/lib/api/client';

vi.mock('../../src/lib/api/sessions', () => ({
  fetchLatestMessages: vi.fn(),
  fetchOlderMessages: vi.fn(),
}));

const latestPage = {
  items: [
    {
      seq: 2,
      kind: 'user_message',
      author: 'Alice',
      content: 'Hello there',
      timestamp: '2026-11-22T14:02:00Z',
    },
    {
      seq: 3,
      kind: 'assistant_message',
      author: 'Octave',
      content: 'Hi Alice!',
      timestamp: '2026-11-22T14:02:05Z',
    },
  ],
  next_cursor: null,
  prev_cursor: 2,
};

beforeEach(() => {
  vi.mocked(fetchLatestMessages).mockReset();
  vi.mocked(fetchOlderMessages).mockReset();
});

describe('SessionChatView', () => {
  it('shows a loading state before the first page resolves', () => {
    vi.mocked(fetchLatestMessages).mockReturnValue(new Promise(() => {}));
    render(<SessionChatView sessionId="s_1" />);
    expect(screen.getByRole('status').textContent).toContain('Loading transcript');
  });

  it('opens at the newest page', async () => {
    vi.mocked(fetchLatestMessages).mockResolvedValue(latestPage);
    render(<SessionChatView sessionId="s_1" />);
    expect(await screen.findByText('Hello there')).toBeDefined();
    expect(screen.getByText('Hi Alice!')).toBeDefined();
    expect(fetchLatestMessages).toHaveBeenCalledWith('s_1');
  });

  it('renders the empty state for a message-less session', async () => {
    vi.mocked(fetchLatestMessages).mockResolvedValue({
      items: [],
      next_cursor: null,
      prev_cursor: null,
    });
    render(<SessionChatView sessionId="s_1" />);
    expect(await screen.findByText(/No messages yet/)).toBeDefined();
  });

  it('shows a not-found error for 404', async () => {
    vi.mocked(fetchLatestMessages).mockRejectedValue(new ApiError(404, 'Session not found'));
    render(<SessionChatView sessionId="s_ghost" />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain("doesn't exist");
  });

  it('shows a forbidden error for 403', async () => {
    vi.mocked(fetchLatestMessages).mockRejectedValue(new ApiError(403, 'Not a member'));
    render(<SessionChatView sessionId="s_1" />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('access');
  });

  it('shows a network error and retries successfully', async () => {
    vi.mocked(fetchLatestMessages)
      .mockRejectedValueOnce(new TypeError('fetch failed'))
      .mockResolvedValueOnce(latestPage);
    render(<SessionChatView sessionId="s_1" />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('reach the server');

    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByText('Hello there')).toBeDefined();
  });

  it('prepends older messages via the load-older button', async () => {
    vi.mocked(fetchLatestMessages).mockResolvedValue(latestPage);
    vi.mocked(fetchOlderMessages).mockResolvedValue({
      items: [
        {
          seq: 1,
          kind: 'user_message',
          author: 'Alice',
          content: 'An older message',
          timestamp: '2026-11-22T13:55:00Z',
        },
      ],
      next_cursor: null,
      prev_cursor: null,
    });
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');

    fireEvent.click(screen.getByRole('button', { name: /load older messages/i }));
    expect(await screen.findByText('An older message')).toBeDefined();
    expect(fetchOlderMessages).toHaveBeenCalledWith('s_1', 2);

    // Older messages appear above the current window.
    const items = screen.getByRole('list').children;
    expect(items[0].textContent).toContain('An older message');
    expect(items[items.length - 1].textContent).toContain('Hi Alice!');

    // Cursor exhausted: the button disappears.
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /load older messages/i })).toBeNull()
    );
  });

  it('reloads when the sessionId prop changes', async () => {
    vi.mocked(fetchLatestMessages).mockResolvedValue(latestPage);
    const { rerender } = render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');

    vi.mocked(fetchLatestMessages).mockResolvedValue({
      items: [
        {
          seq: 9,
          kind: 'user_message',
          author: 'Bob',
          content: 'Different session',
          timestamp: '2026-11-22T15:00:00Z',
        },
      ],
      next_cursor: null,
      prev_cursor: null,
    });
    rerender(<SessionChatView sessionId="s_2" />);

    expect(await screen.findByText('Different session')).toBeDefined();
    expect(screen.queryByText('Hello there')).toBeNull();
    expect(fetchLatestMessages).toHaveBeenLastCalledWith('s_2');
  });
});
