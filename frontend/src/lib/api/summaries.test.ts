import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  fetchSessionSummaries,
  fetchSessionSummaryDetail,
  refreshSessionSummary,
} from './summaries';

const fetchMock = vi.fn();

function jsonResponse(payload: unknown) {
  return {
    ok: true,
    status: 200,
    statusText: 'OK',
    text: async () => JSON.stringify(payload),
  } as Response;
}

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe('summaries api', () => {
  it('fetches the summary list page', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ items: [], total: 0, limit: 20, offset: 0 })
    );
    const page = await fetchSessionSummaries(20, 0);
    expect(page.total).toBe(0);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      { method?: string; credentials?: string },
    ];
    expect(url).toContain('/api/session-summaries?limit=20&offset=0');
    expect(init.method).toBe('GET');
    expect(init.credentials).toBe('include');
  });

  it('fetches one session summary detail', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ session_id: 's/1', summary: null })
    );
    const res = await fetchSessionSummaryDetail('s/1');
    expect(res.session_id).toBe('s/1');
    const [url] = fetchMock.mock.calls[0] as unknown as [string];
    expect(url).toContain('/api/session-summaries/s%2F1');
  });

  it('refreshes a session summary via POST', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        session_id: 's_1',
        summary: {
          title: 'T',
          content: 'C',
          model_name: null,
          generated_at: '2026-11-26T00:00:00Z',
        },
      })
    );
    const res = await refreshSessionSummary('s_1');
    expect(res.summary?.title).toBe('T');
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      { method?: string },
    ];
    expect(url).toContain('/api/session-summaries/s_1/refresh');
    expect(init.method).toBe('POST');
  });
});
