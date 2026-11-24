import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  fetchLatestMessages,
  fetchOlderMessages,
  fetchSessions,
  createSession,
  deleteSession,
} from './sessions';

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

const sessionsPage = JSON.stringify({
  items: [
    {
      id: 's_1',
      title: 'Trip planning',
      created_at: '2026-11-24T10:00:00Z',
      last_activity: '2026-11-24T12:00:00Z',
      last_message: 'yes, lets go',
    },
  ],
  total: 1,
  limit: 50,
  offset: 0,
});

describe('fetchSessions', () => {
  it('requests the default page (limit 50, offset 0)', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, text: async () => sessionsPage });
    const page = await fetchSessions();
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/sessions?limit=50&offset=0',
      expect.objectContaining({ method: 'GET', credentials: 'include' })
    );
    expect(page.items[0].id).toBe('s_1');
    expect(page.total).toBe(1);
  });

  it('passes custom limit/offset', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, text: async () => sessionsPage });
    await fetchSessions(10, 20);
    expect(mockFetch.mock.calls[0][0]).toContain('limit=10&offset=20');
  });
});

describe('createSession', () => {
  it('POSTs with an explicit null title when omitted', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: async () =>
        JSON.stringify({
          id: 's_2',
          title: null,
          created_at: '2026-11-24T13:00:00Z',
          last_activity: '2026-11-24T13:00:00Z',
          last_message: null,
        }),
    });
    const created = await createSession();
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/sessions',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        body: JSON.stringify({ title: null }),
      })
    );
    expect(created.id).toBe('s_2');
  });

  it('sends the title when provided', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, text: async () => sessionsPage });
    await createSession('Groceries');
    expect(JSON.parse(mockFetch.mock.calls[0][1].body)).toEqual({ title: 'Groceries' });
  });
});

describe('deleteSession', () => {
  it('DELETEs the encoded session path', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, text: async () => '' });
    await deleteSession('a b/c');
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/sessions/a%20b%2Fc',
      expect.objectContaining({ method: 'DELETE', credentials: 'include' })
    );
  });

  it('rejects with ApiError when the endpoint is missing (backend issue pending)', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 405,
      statusText: 'Method Not Allowed',
      text: async () => '',
    });
    await expect(deleteSession('s_1')).rejects.toMatchObject({ status: 405 });
  });
});
