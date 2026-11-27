import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { ApiError } from './client';
import { useSessionSummaries } from './useSessionSummaries';
import * as summariesApi from './summaries';

vi.mock('./summaries');

const row = (id: string): summariesApi.SessionSummaryListItem => ({
  id,
  title: `Chat ${id}`,
  created_at: '2026-11-01T00:00:00Z',
  last_activity: '2026-11-24T12:00:00Z',
  last_message: 'preview',
  summary: null,
});

const page = (ids: string[], total: number): summariesApi.SessionSummaryListPage => ({
  items: ids.map(row),
  total,
  limit: 20,
  offset: 0,
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useSessionSummaries', () => {
  it('loads the first page on mount', async () => {
    vi.mocked(summariesApi.fetchSessionSummaries).mockResolvedValue(page(['a', 'b'], 2));
    const { result } = renderHook(() => useSessionSummaries());
    expect(result.current.state).toBe('loading');
    await waitFor(() => expect(result.current.state).toBe('ready'));
    expect(result.current.items.map((i) => i.id)).toEqual(['a', 'b']);
    expect(result.current.hasMore).toBe(false);
  });

  it('surfaces fetch errors with a working retry', async () => {
    vi.mocked(summariesApi.fetchSessionSummaries)
      .mockRejectedValueOnce(new ApiError(500, 'boom'))
      .mockResolvedValueOnce(page(['a'], 1));
    const { result } = renderHook(() => useSessionSummaries());
    await waitFor(() => expect(result.current.state).toBe('error'));
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.state).toBe('ready'));
  });

  it('regenerate patches the row summary in place', async () => {
    vi.mocked(summariesApi.fetchSessionSummaries).mockResolvedValue(page(['a'], 1));
    vi.mocked(summariesApi.refreshSessionSummary).mockResolvedValue({
      session_id: 'a',
      summary: {
        title: 'T',
        content: 'C',
        model_name: 'm',
        generated_at: '2026-11-26T00:00:00Z',
      },
    });
    const { result } = renderHook(() => useSessionSummaries());
    await waitFor(() => expect(result.current.state).toBe('ready'));
    await act(async () => {
      await result.current.regenerate('a');
    });
    expect(result.current.items[0].summary?.title).toBe('T');
  });

  it('regenerate throws and sets the error on failure', async () => {
    vi.mocked(summariesApi.fetchSessionSummaries).mockResolvedValue(page(['a'], 1));
    vi.mocked(summariesApi.refreshSessionSummary).mockRejectedValue(new ApiError(503, 'off'));
    const { result } = renderHook(() => useSessionSummaries());
    await waitFor(() => expect(result.current.state).toBe('ready'));
    await act(async () => {
      await expect(result.current.regenerate('a')).rejects.toThrow();
    });
    expect(result.current.error).toContain("Couldn't generate summary");
  });

  it('loadOlder appends the next page', async () => {
    vi.mocked(summariesApi.fetchSessionSummaries)
      .mockResolvedValueOnce(page(['a'], 2))
      .mockResolvedValueOnce({
        ...page(['b'], 2),
        offset: 1,
        items: [row('b')],
      });
    const { result } = renderHook(() => useSessionSummaries(1));
    await waitFor(() => expect(result.current.state).toBe('ready'));
    expect(result.current.hasMore).toBe(true);
    act(() => result.current.loadOlder());
    await waitFor(() => expect(result.current.items.map((i) => i.id)).toEqual(['a', 'b']));
  });
});
