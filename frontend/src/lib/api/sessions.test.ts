import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fetchLatestMessages, fetchOlderMessages } from './sessions';

// Mock global fetch (same approach as client.test.ts): asserts the exact
// URL each helper builds against the backend contract.
const mockFetch = vi.fn();
(globalThis as unknown as { fetch: typeof fetch }).fetch = mockFetch;

const emptyPage = JSON.stringify({ items: [], next_cursor: null, prev_cursor: null });

beforeEach(() => {
  vi.clearAllMocks();
  mockFetch.mockResolvedValue({ ok: true, text: async () => emptyPage });
});

describe('fetchLatestMessages', () => {
  it('requests the newest page via end=true', async () => {
    await fetchLatestMessages('s_1');
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/sessions/s_1/messages?end=true',
      expect.objectContaining({ method: 'GET', credentials: 'include' })
    );
  });

  it('URL-encodes the session id', async () => {
    await fetchLatestMessages('a b/c');
    expect(mockFetch.mock.calls[0][0]).toContain('/api/sessions/a%20b%2Fc/messages');
  });

  it('parses the transcript page envelope', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: async () =>
        JSON.stringify({
          items: [
            {
              seq: 5,
              kind: 'assistant_message',
              author: 'Octave',
              content: 'hi',
              timestamp: '2026-11-22T14:00:00Z',
            },
          ],
          next_cursor: null,
          prev_cursor: 5,
        }),
    });
    const page = await fetchLatestMessages('s_1');
    expect(page.items[0].seq).toBe(5);
    expect(page.prev_cursor).toBe(5);
  });
});

describe('fetchOlderMessages', () => {
  it('requests the page below before_seq', async () => {
    await fetchOlderMessages('s_1', 42);
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/sessions/s_1/messages?before_seq=42',
      expect.objectContaining({ method: 'GET', credentials: 'include' })
    );
  });

  it('URL-encodes the session id', async () => {
    await fetchOlderMessages('a b', 7);
    expect(mockFetch.mock.calls[0][0]).toContain('/api/sessions/a%20b/messages');
  });
});
