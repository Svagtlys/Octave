// Conversation-list data hook (issue #46). Owns fetch/create/delete state for
// the sessions list; navigation side effects live in the consuming views.
// remove() THROWS on failure (the caller must not navigate); create() returns
// null on failure (inline error only). Stale-response guard mirrors
// SessionChatView.loadLatest.
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './client';
import {
  createSession,
  deleteSession,
  fetchSessions,
  type SessionSummary,
} from './sessions';

export type ConversationsState = 'loading' | 'ready' | 'error';

export interface UseConversations {
  sessions: SessionSummary[];
  state: ConversationsState;
  error: string | null;
  hasMore: boolean;
  loadingOlder: boolean;
  refresh: () => void;
  /** POST + prepend; resolves to the new id, or null when the POST failed. */
  create: () => Promise<string | null>;
  /** DELETE + local removal. Throws on failure; resolves to the next most
   * recent remaining session (list is activity-ordered) or null. */
  remove: (id: string) => Promise<SessionSummary | null>;
  loadOlder: () => void;
}

export function describeListError(error: unknown, action: string): string {
  if (error instanceof ApiError) {
    return `Couldn't ${action} (${error.status}).`;
  }
  return `Couldn't ${action} — server unreachable.`;
}

export function useConversations(pageSize = 50): UseConversations {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [state, setState] = useState<ConversationsState>('loading');
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const requestRef = useRef(0);
  const offsetRef = useRef(0);
  // Mirror of `sessions` for synchronous reads inside callbacks: the
  // setSessions updater is NOT invoked synchronously, so computing `next`
  // inside it would leave remove()'s return value null. All mutations flow
  // through this hook, keeping the ref in sync is cheap and total.
  const sessionsRef = useRef<SessionSummary[]>([]);

  const refresh = useCallback(() => {
    const requestId = ++requestRef.current;
    offsetRef.current = 0;
    setState('loading');
    setError(null);
    fetchSessions(pageSize, 0)
      .then((page) => {
        if (requestRef.current !== requestId) return;
        sessionsRef.current = page.items;
        setSessions(page.items);
        setTotal(page.total);
        setState('ready');
      })
      .catch((err: unknown) => {
        if (requestRef.current !== requestId) return;
        setError(describeListError(err, 'load conversations'));
        setState('error');
      });
  }, [pageSize]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const create = useCallback(async (): Promise<string | null> => {
    try {
      const created = await createSession();
      sessionsRef.current = [created, ...sessionsRef.current];
      setSessions(sessionsRef.current);
      setTotal((t) => t + 1);
      return created.id;
    } catch (err) {
      setError(describeListError(err, 'create conversation'));
      return null;
    }
  }, []);

  const remove = useCallback(async (id: string): Promise<SessionSummary | null> => {
    try {
      await deleteSession(id);
    } catch (err) {
      setError(describeListError(err, 'delete conversation'));
      throw err;
    }
    const remaining = sessionsRef.current.filter((s) => s.id !== id);
    sessionsRef.current = remaining;
    setSessions(remaining);
    setTotal((t) => Math.max(0, t - 1));
    return remaining[0] ?? null;
  }, []);

  const loadOlder = useCallback(() => {
    const nextOffset = sessions.length;
    if (loadingOlder || nextOffset >= total) return;
    const requestId = requestRef.current;
    setLoadingOlder(true);
    fetchSessions(pageSize, nextOffset)
      .then((page) => {
        if (requestRef.current !== requestId) return;
        offsetRef.current = nextOffset + page.items.length;
        sessionsRef.current = [...sessionsRef.current, ...page.items];
        setSessions(sessionsRef.current);
        setTotal(page.total);
      })
      .catch((err: unknown) => {
        if (requestRef.current !== requestId) return;
        setError(describeListError(err, 'load older conversations'));
      })
      .finally(() => setLoadingOlder(false));
  }, [sessions.length, total, loadingOlder, pageSize]);

  return {
    sessions,
    state,
    error,
    hasMore: sessions.length < total,
    loadingOlder,
    refresh,
    create,
    remove,
    loadOlder,
  };
}
