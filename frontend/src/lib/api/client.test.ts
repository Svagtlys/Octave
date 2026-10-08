import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get, post, login, setUnauthorizedHandler, ApiError } from './client';

// Mock global fetch
const mockFetch = vi.fn();
(globalThis as unknown as { fetch: typeof fetch }).fetch = mockFetch;

describe('API client', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('parses JSON on success', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: async () => JSON.stringify({ status: 'ok' }),
    });

    const result = await get<{ status: string }>('/api/health');
    expect(result).toEqual({ status: 'ok' });
  });

  it('throws ApiError on 404', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: 'Not Found',
      text: async () => '',
    });

    try {
      await get('/api/nonexistent');
      expect.fail('Expected ApiError to be thrown');
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(404);
    }
  });

  it('throws ApiError on 500', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
      text: async () => '',
    });

    try {
      await get('/api/boom');
      expect.fail('Expected ApiError to be thrown');
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(500);
    }
  });

  it('uses BASE_URL from env', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      text: async () => '',
    });

    await get('/api/health');
    expect(mockFetch).toHaveBeenCalledWith(
      'http://localhost:8000/api/health',
      expect.objectContaining({ method: 'GET' })
    );
  });

  it('sends credentials: include on every request', async () => {
    mockFetch.mockResolvedValue({
      ok: true,
      text: async () => '',
    });

    await get('/api/anything');
    await post('/api/anything', {});
    for (const call of mockFetch.mock.calls) {
      expect(call[1]).toEqual(expect.objectContaining({ credentials: 'include' }));
    }
  });

  it('triggers onUnauthorized on 401 for ordinary paths', async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      text: async () => '',
    });

    await expect(get('/api/sessions')).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledTimes(1);
    setUnauthorizedHandler(null);
  });

  it('does not trigger onUnauthorized for auth endpoints (login 401 is form feedback)', async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      statusText: 'Unauthorized',
      text: async () => 'Invalid credentials',
    });

    await expect(login('alice', 'wrong')).rejects.toBeInstanceOf(ApiError);
    expect(handler).not.toHaveBeenCalled();
    setUnauthorizedHandler(null);
  });
});
