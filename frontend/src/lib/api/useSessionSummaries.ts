// Session-summary list hook (issue #112). Same discipline as
// useConversations: stale-response guard via requestRef, a ref mirror for
// synchronous reads inside callbacks, and errors that never silently pass.
import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from './client';
import {
  fetchSessionSummaries,
  refreshSessionSummary,
  type SessionSummaryListItem,
  type SummaryDetail,
} from './summaries';

export type SummariesState = 'loading' | 'ready' | 'error';

export interface UseSessionSummaries {
  items: SessionSummaryListItem[];
  state: SummariesState;
  error: string | null;
  hasMore: boolean;
  loadingOlder: boolean;
  refresh: () => void;
  loadOlder: () => void;
  /** POST refresh + patch the row's summary in place. Throws on failure;
   * resolves to the fresh detail (null for an empty transcript). */
  regenerate: (sessionId: string) => Promise<SummaryDetail | null>;
}

function describeError(error: unknown, action: string): string {
  if (error instanceof ApiError) {
    return `Couldn't ${action} (${error.status}).`;
  }
  return `Couldn't ${action} — server unreachable.`;
}

export function useSessionSummaries(pageSize = 20): UseSessionSummaries {
  const [items, setItems] = useState<SessionSummaryListItem[]>([]);
  const [state, setState] = useState<SummariesState>('loading');
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const requestRef = useRef(0);
  const itemsRef = useRef<SessionSummaryListItem[]>([]);

  const refresh = useCallback(() => {
    const requestId = ++requestRef.current;
    setState('loading');
    setError(null);
    fetchSessionSummaries(pageSize, 0)
      .then((page) => {
        if (requestRef.current !== requestId) return;
        itemsRef.current = page.items;
        setItems(page.items);
        setTotal(page.total);
        setState('ready');
      })
      .catch((err: unknown) => {
        if (requestRef.current !== requestId) return;
        setError(describeError(err, 'load summaries'));
        setState('error');
      });
  }, [pageSize]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const loadOlder = useCallback(() => {
    const nextOffset = itemsRef.current.length;
    if (loadingOlder || nextOffset >= total) return;
    const requestId = requestRef.current;
    setLoadingOlder(true);
    fetchSessionSummaries(pageSize, nextOffset)
      .then((page) => {
        if (requestRef.current !== requestId) return;
        itemsRef.current = [...itemsRef.current, ...page.items];
        setItems(itemsRef.current);
        setTotal(page.total);
      })
      .catch((err: unknown) => {
        if (requestRef.current !== requestId) return;
        setError(describeError(err, 'load older summaries'));
      })
      .finally(() => setLoadingOlder(false));
  }, [loadingOlder, total, pageSize]);

  const regenerate = useCallback(
    async (sessionId: string): Promise<SummaryDetail | null> => {
      let response;
      try {
        response = await refreshSessionSummary(sessionId);
      } catch (err) {
        setError(describeError(err, 'generate summary'));
        throw err;
      }
      const summary = response.summary;
      itemsRef.current = itemsRef.current.map((item) =>
        item.id === sessionId
          ? {
              ...item,
              summary: summary
                ? {
                    title: summary.title,
                    snippet:
                      summary.content.length <= 140
                        ? summary.content
                        : summary.content.slice(0, 139) + '…',
                    model_name: summary.model_name,
                    generated_at: summary.generated_at,
                  }
                : null,
            }
          : item
      );
      setItems(itemsRef.current);
      return summary;
    },
    []
  );

  return {
    items,
    state,
    error,
    hasMore: items.length < total,
    loadingOlder,
    refresh,
    loadOlder,
    regenerate,
  };
}
