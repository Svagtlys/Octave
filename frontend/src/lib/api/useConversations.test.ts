import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { ApiError } from './client';
import { useConversations } from './useConversations';
import * as sessionsApi from './sessions';

vi.mock('./sessions');

const row = (id: string, activity: string): sessionsApi.SessionSummary => ({
  id,
  title: `Chat ${id}`,
  created_at: '2026-11-01T00:00:00Z',
  last_activity: activity,
  last_message: 'preview',
});

beforeEach(() => {
  vi.clearAllMocks();
});

describe('useConversations', () => {
  it('loads the first page on mount', async () => {
    vi.mocked(sessionsApi.fetchSessions).mockResolvedValue({
      items: [row('a', '2026-11-24T12:00:00Z'), row('b', '2026-11-23T12:00:00Z')],
      total: 2,
      limit: 50,
      offset: 0,
    });
    const { result } = renderHook(() => useConversations());
    expect(result.current.state).toBe('loading');
    await waitFor(() => expect(result.current.state).toBe('ready'));
    expect(result.current.sessions.map((s) => s.id)).toEqual(['a', 'b']);
    expect(result.current.hasMore).toBe(false);
  });

  it('surfaces fetch errors with a working retry', async () => {
    vi.mocked(sessionsApi.fetchSessions)
      .mockRejectedValueOnce(new ApiError(500, 'boom'))
      .mockResolvedValueOnce({
        items: [row('a', '2026-11-24T12:00:00Z')],
        total: 1,
        limit: 50,
        offset: 0,
      });
    const { result } = renderHook(() => useConversations());
    await waitFor(() => expect(result.current.state).toBe('error'));
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.state).toBe('ready'));
    expect(result.current.sessions).toHaveLength(1);
  });

  it('create prepends the new row and returns its id', async () => {
    vi.mocked(sessionsApi.fetchSessions).mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    vi.mocked(sessionsApi.createSession).mockResolvedValue(row('new', '2026-11-24T13:00:00Z'));
    const { result } = renderHook(() => useConversations());
    await waitFor(() => expect(result.current.state).toBe('ready'));
    let id: string | null = null;
    await act(async () => {
      id = await result.current.create();
    });
    expect(id).toBe('new');
    expect(result.current.sessions[0].id).toBe('new');
  });

  it('create failure returns null and sets the error', async () => {
    vi.mocked(sessionsApi.fetchSessions).mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    vi.mocked(sessionsApi.createSession).mockRejectedValue(new ApiError(500, 'nope'));
    const { result } = renderHook(() => useConversations());
    await waitFor(() => expect(result.current.state).toBe('ready'));
    let id: string | null = 'unset';
    await act(async () => {
      id = await result.current.create();
    });
    expect(id).toBeNull();
    expect(result.current.error).toMatch(/create/i);
  });

  it('remove deletes the row and returns the next most recent', async () => {
    vi.mocked(sessionsApi.fetchSessions).mockResolvedValue({
      items: [row('a', '2026-11-24T12:00:00Z'), row('b', '2026-11-23T12:00:00Z')],
      total: 2,
      limit: 50,
      offset: 0,
    });
    vi.mocked(sessionsApi.deleteSession).mockResolvedValue();
    const { result } = renderHook(() => useConversations());
    await waitFor(() => expect(result.current.state).toBe('ready'));
    const seen: { next: sessionsApi.SessionSummary | null } = { next: null };
    await act(async () => {
      seen.next = await result.current.remove('a');
    });
    expect(seen.next?.id).toBe('b');
    expect(result.current.sessions.map((s) => s.id)).toEqual(['b']);
  });

  it('remove of the last session returns null', async () => {
    vi.mocked(sessionsApi.fetchSessions).mockResolvedValue({
      items: [row('a', '2026-11-24T12:00:00Z')],
      total: 1,
      limit: 50,
      offset: 0,
    });
    vi.mocked(sessionsApi.deleteSession).mockResolvedValue();
    const { result } = renderHook(() => useConversations());
    await waitFor(() => expect(result.current.state).toBe('ready'));
    const seen: { next: sessionsApi.SessionSummary | null } = {
      next: { id: 'x' } as sessionsApi.SessionSummary,
    };
    await act(async () => {
      seen.next = await result.current.remove('a');
    });
    expect(seen.next).toBeNull();
  });

  it('remove failure throws, keeps the row, sets the error', async () => {
    vi.mocked(sessionsApi.fetchSessions).mockResolvedValue({
      items: [row('a', '2026-11-24T12:00:00Z')],
      total: 1,
      limit: 50,
      offset: 0,
    });
    vi.mocked(sessionsApi.deleteSession).mockRejectedValue(new ApiError(405, 'Method Not Allowed'));
    const { result } = renderHook(() => useConversations());
    await waitFor(() => expect(result.current.state).toBe('ready'));
    await act(async () => {
      await expect(result.current.remove('a')).rejects.toMatchObject({ status: 405 });
    });
    expect(result.current.sessions).toHaveLength(1);
    expect(result.current.error).toMatch(/delete/i);
  });

  it('loadOlder appends the next page while total is not reached', async () => {
    vi.mocked(sessionsApi.fetchSessions)
      .mockResolvedValueOnce({
        items: [row('a', '2026-11-24T12:00:00Z')],
        total: 2,
        limit: 1,
        offset: 0,
      })
      .mockResolvedValueOnce({
        items: [row('b', '2026-11-23T12:00:00Z')],
        total: 2,
        limit: 1,
        offset: 1,
      });
    const { result } = renderHook(() => useConversations(1));
    await waitFor(() => expect(result.current.hasMore).toBe(true));
    act(() => result.current.loadOlder());
    await waitFor(() => expect(result.current.hasMore).toBe(false));
    expect(result.current.sessions.map((s) => s.id)).toEqual(['a', 'b']);
    expect(sessionsApi.fetchSessions).toHaveBeenLastCalledWith(1, 1);
  });
});
