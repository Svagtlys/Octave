import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { act } from 'react';
import type { MutableRefObject } from 'react';
import SessionChatView from '../../src/views/SessionChatView';
import { fetchLatestMessages, fetchOlderMessages } from '../../src/lib/api/sessions';
import { ApiError } from '../../src/lib/api/client';
import {
  useSessionSocket,
  type SocketFrame,
  type SocketStatus,
} from '../../src/lib/api/useSessionSocket';

vi.mock('../../src/lib/api/sessions', () => ({
  fetchLatestMessages: vi.fn(),
  fetchOlderMessages: vi.fn(),
}));

vi.mock('../../src/lib/api/useSessionSocket', () => ({
  useSessionSocket: vi.fn(),
}));

const sendUserMessage = vi.fn();
let frameSink: ((frame: SocketFrame) => void) | null = null;
let socketStatus: SocketStatus = 'open';

function pushFrame(frame: SocketFrame) {
  act(() => {
    frameSink?.(frame);
  });
}

function eventFrame(overrides: Partial<SocketFrame> = {}): SocketFrame {
  return {
    type: 'event',
    session_id: 's_1',
    seq: 4,
    kind: 'user_message',
    author: 'Alice',
    content: 'Echoed message',
    ...overrides,
  } as SocketFrame;
}

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
  frameSink = null;
  socketStatus = 'open';
  sendUserMessage.mockReset();
  vi.mocked(useSessionSocket).mockImplementation(
    (_sid, sinkRef: MutableRefObject<((frame: SocketFrame) => void) | null>) => {
      frameSink = sinkRef.current;
      return { status: socketStatus, sendUserMessage };
    }
  );
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

describe('SessionChatView composer', () => {
  beforeEach(async () => {
    vi.mocked(fetchLatestMessages).mockResolvedValue(latestPage);
  });

  it('renders the composer below the transcript', async () => {
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');
    expect(screen.getByRole('textbox', { name: /message input/i })).toBeDefined();
    expect(screen.getByRole('button', { name: /^send$/i })).toBeDefined();
  });

  it('Send calls sendUserMessage and shows the waiting status', async () => {
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');
    await userEvent.type(screen.getByRole('textbox', { name: /message input/i }), 'Hi agent!');
    await userEvent.click(screen.getByRole('button', { name: /^send$/i }));
    expect(sendUserMessage).toHaveBeenCalledWith('Hi agent!');
    expect(screen.getByRole('status').textContent).toContain('thinking');
  });

  it('renders the sender echo when the user_message event frame arrives', async () => {
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');
    pushFrame(eventFrame({ seq: 7, content: 'Echoed message' }));
    expect(await screen.findByText('Echoed message')).toBeDefined();
  });

  it('assistant_message frame clears the waiting status', async () => {
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');
    await userEvent.type(screen.getByRole('textbox', { name: /message input/i }), 'Hi');
    await userEvent.click(screen.getByRole('button', { name: /^send$/i }));
    expect(screen.getByRole('status').textContent).toContain('thinking');
    pushFrame(
      eventFrame({ seq: 8, kind: 'assistant_message', author: 'Octave', content: 'Reply!' })
    );
    expect(await screen.findByText('Reply!')).toBeDefined();
    expect(screen.queryByText(/thinking/)).toBeNull();
  });

  it('ignores non-chat event kinds', async () => {
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');
    pushFrame(eventFrame({ seq: 8, kind: 'tool_call', content: 'filesystem:read' }));
    pushFrame(eventFrame({ seq: 9, kind: 'system', content: 'internal note' }));
    expect(screen.queryByText('filesystem:read')).toBeNull();
    expect(screen.queryByText('internal note')).toBeNull();
  });

  it('dedupes by seq — a repeated frame renders one row', async () => {
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');
    pushFrame(eventFrame({ seq: 7, content: 'Only once' }));
    pushFrame(eventFrame({ seq: 7, content: 'Only once' }));
    const rows = Array.from(screen.getAllByText('Only once'));
    expect(rows).toHaveLength(1);
  });

  it('error frame shows an alert and clears the waiting status', async () => {
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');
    await userEvent.type(screen.getByRole('textbox', { name: /message input/i }), 'Hi');
    await userEvent.click(screen.getByRole('button', { name: /^send$/i }));
    pushFrame({ type: 'error', error: 'request pipeline not configured' });
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('pipeline');
    expect(screen.queryByText(/thinking/)).toBeNull();
  });

  it('composer is disabled when the socket is not open', async () => {
    socketStatus = 'closed';
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');
    expect(
      (screen.getByRole('textbox', { name: /message input/i }) as HTMLTextAreaElement).disabled
    ).toBe(true);
    expect((screen.getByRole('button', { name: /^send$/i }) as HTMLButtonElement).disabled).toBe(
      true
    );
    expect(screen.getByRole('status').textContent).toContain('Disconnected');
  });

  it('re-fetches the newest page when the socket reconnects', async () => {
    const view = render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');
    expect(fetchLatestMessages).toHaveBeenCalledTimes(1);
    socketStatus = 'closed';
    view.rerender(<SessionChatView sessionId="s_1" />);
    socketStatus = 'open';
    view.rerender(<SessionChatView sessionId="s_1" />);
    await waitFor(() => expect(fetchLatestMessages).toHaveBeenCalledTimes(2));
  });

  it('streams thinking and text into a provisional bubble', async () => {
    vi.mocked(fetchLatestMessages).mockResolvedValue(latestPage);
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');

    await userEvent.type(screen.getByRole('textbox', { name: /message input/i }), 'go');
    await userEvent.click(screen.getByRole('button', { name: /^send$/i }));
    expect(screen.getByText(/Agent is thinking/)).toBeDefined();

    pushFrame({ type: 'stream_thinking', session_id: 's_1', round: 0, delta: 'hmm ' });
    pushFrame({ type: 'stream_text', session_id: 's_1', round: 0, delta: 'Hel' });
    pushFrame({ type: 'stream_text', session_id: 's_1', round: 0, delta: 'lo' });

    expect(screen.queryByText(/Agent is thinking/)).toBeNull();
    expect(screen.getByText('Hello')).toBeDefined();
    expect(screen.getByText('hmm')).toBeDefined();
  });

  it('committed assistant_message replaces the provisional bubble', async () => {
    vi.mocked(fetchLatestMessages).mockResolvedValue(latestPage);
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');

    pushFrame({ type: 'stream_text', session_id: 's_1', round: 0, delta: 'partial' });
    pushFrame(
      eventFrame({ kind: 'assistant_message', seq: 9, content: 'Final answer' })
    );

    expect(screen.getByText('Final answer')).toBeDefined();
    expect(screen.queryByText('partial')).toBeNull();
  });

  it('renders tool cards from stream frames and keeps them after commit', async () => {
    vi.mocked(fetchLatestMessages).mockResolvedValue(latestPage);
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');

    pushFrame({
      type: 'stream_tool_start',
      session_id: 's_1',
      round: 0,
      index: 0,
      call_id: 'c1',
      name: 'mcp__fs__write',
    });
    pushFrame({
      type: 'stream_tool_args',
      session_id: 's_1',
      round: 0,
      index: 0,
      arguments: '{"a": 1}',
    });
    expect(screen.getByText('mcp__fs__write')).toBeDefined();
    expect(screen.getByText('calling tool')).toBeDefined();

    pushFrame({
      type: 'stream_tool_result',
      session_id: 's_1',
      round: 0,
      call_id: 'c1',
      name: 'mcp__fs__write',
      content: 'ok',
      is_error: false,
    });
    expect(screen.getByText('done')).toBeDefined();
    expect(screen.getByText('ok')).toBeDefined();

    pushFrame(
      eventFrame({ kind: 'assistant_message', seq: 9, content: 'File written.' })
    );
    // Cards survive the commit (spec Decision 5); the provisional text is gone.
    expect(screen.getByText('mcp__fs__write')).toBeDefined();
  });

  it('error frames clear provisional state', async () => {
    vi.mocked(fetchLatestMessages).mockResolvedValue(latestPage);
    render(<SessionChatView sessionId="s_1" />);
    await screen.findByText('Hello there');

    pushFrame({ type: 'stream_text', session_id: 's_1', round: 0, delta: 'partial' });
    pushFrame({ type: 'error', error: 'boom' });
    expect(screen.queryByText('partial')).toBeNull();
  });
});
