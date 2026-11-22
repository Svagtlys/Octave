import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import SessionChatView from '../../src/views/SessionChatView';
import { fetchSessionMessages } from '../../src/lib/api/sessions';
import { ApiError } from '../../src/lib/api/client';

vi.mock('../../src/lib/api/sessions', () => ({
  fetchSessionMessages: vi.fn(),
}));

const page1 = {
  items: [
    {
      seq: 1,
      kind: 'user_message',
      author: 'Alice',
      content: 'Hello there',
      timestamp: '2026-11-22T14:02:00Z',
    },
    {
      seq: 2,
      kind: 'assistant_message',
      author: 'Octave',
      content: 'Hi Alice!',
      timestamp: '2026-11-22T14:02:05Z',
    },
  ],
  next_cursor: 2,
};

beforeEach(() => {
  vi.mocked(fetchSessionMessages).mockReset();
});

describe('SessionChatView', () => {
  it('shows a loading state before the first page resolves', () => {
    vi.mocked(fetchSessionMessages).mockReturnValue(new Promise(() => {}));
    render(<SessionChatView sessionId="s_1" />);
    expect(screen.getByRole('status').textContent).toContain('Loading transcript');
  });

  it('renders the transcript after loading', async () => {
    vi.mocked(fetchSessionMessages).mockResolvedValue(page1);
    render(<SessionChatView sessionId="s_1" />);
    expect(await screen.findByText('Hello there')).toBeDefined();
    expect(screen.getByText('Hi Alice!')).toBeDefined();
    expect(fetchSessionMessages).toHaveBeenCalledWith('s_1');
  });

  it('renders the empty state for a message-less session', async () => {
    vi.mocked(fetchSessionMessages).mockResolvedValue({ items: [], next_cursor: null });
    render(<SessionChatView sessionId="s_1" />);
    expect(await screen.findByText(/No messages yet/)).toBeDefined();
  });

  it('shows a not-found error for 404', async () => {
    vi.mocked(fetchSessionMessages).mockRejectedValue(new ApiError(404, 'Session not found'));
    render(<SessionChatView sessionId="s_ghost" />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain("doesn't exist");
  });

  it('shows a forbidden error for 403', async () => {
    vi.mocked(fetchSessionMessages).mockRejectedValue(new ApiError(403, 'Not a member'));
    render(<SessionChatView sessionId="s_1" />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('access');
  });

  it('shows a network error and retries successfully', async () => {
    vi.mocked(fetchSessionMessages)
      .mockRejectedValueOnce(new TypeError('fetch failed'))
      .mockResolvedValueOnce(page1);
    render(<SessionChatView sessionId="s_1" />);
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('reach the server');

    fireEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(await screen.findByText('Hello there')).toBeDefined();
  });

  it('appends the next page via the load-newer button', async () => {
    vi.mocked(fetchSessionMessages)
      .mockResolvedValueOnce(page1)
      .mockResolvedValueOnce({
        items: [
          {
            seq: 3,
            kind: 'user_message',
            author: 'Alice',
            content: 'And another thing',
            timestamp: '2026-11-22T14:10:00Z',
          },
        ],
        next_cursor: null,
      });
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');

    fireEvent.click(screen.getByRole('button', { name: /load newer messages/i }));
    expect(await screen.findByText('And another thing')).toBeDefined();
    expect(fetchSessionMessages).toHaveBeenLastCalledWith('s_1', 2);
    // Cursor exhausted: the button disappears.
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: /load newer messages/i })).toBeNull()
    );
  });

  it('reloads when the sessionId prop changes', async () => {
    vi.mocked(fetchSessionMessages).mockResolvedValue(page1);
    const { rerender } = render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');

    vi.mocked(fetchSessionMessages).mockResolvedValue({
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
    });
    rerender(<SessionChatView sessionId="s_2" />);

    expect(await screen.findByText('Different session')).toBeDefined();
    expect(screen.queryByText('Hello there')).toBeNull();
    expect(fetchSessionMessages).toHaveBeenLastCalledWith('s_2');
  });
});
