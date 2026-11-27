// Agent Manager result viewer (issue #112): master–detail over the caller's
// sessions and their cached session summaries. Selecting a row peeks the
// cached summary (never triggers inference); Refresh runs collect(force=True).
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { Button } from '../components/ui';
import { ApiError } from '../lib/api/client';
import { useSessionSummaries } from '../lib/api/useSessionSummaries';
import { fetchSessionSummaryDetail, type SummaryDetail } from '../lib/api/summaries';
import styles from './AgentsView.module.css';

function describeError(error: unknown, action: string): string {
  if (error instanceof ApiError) {
    return `Couldn't ${action} (${error.status}).`;
  }
  return `Couldn't ${action} — server unreachable.`;
}

export default function AgentsView() {
  const navigate = useNavigate();
  const { items, state, error, hasMore, loadingOlder, refresh, loadOlder, regenerate } =
    useSessionSummaries();

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SummaryDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const detailRequestRef = useRef(0);

  const loadDetail = useCallback((sessionId: string) => {
    const requestId = ++detailRequestRef.current;
    setDetailLoading(true);
    setDetailError(null);
    setDetail(null);
    fetchSessionSummaryDetail(sessionId)
      .then((res) => {
        if (detailRequestRef.current !== requestId) return;
        setDetail(res.summary);
      })
      .catch((err: unknown) => {
        if (detailRequestRef.current !== requestId) return;
        setDetailError(describeError(err, 'load summary'));
      })
      .finally(() => {
        if (detailRequestRef.current === requestId) setDetailLoading(false);
      });
  }, []);

  const onSelect = useCallback(
    (sessionId: string) => {
      setSelectedId(sessionId);
      loadDetail(sessionId);
    },
    [loadDetail]
  );

  const onRefresh = useCallback(() => {
    if (selectedId === null) return;
    setRefreshing(true);
    setDetailError(null);
    regenerate(selectedId)
      .then((summary) => {
        // Keep the open detail in sync with the freshly generated summary.
        if (summary !== null) setDetail(summary);
      })
      .catch((err: unknown) => setDetailError(describeError(err, 'generate summary')))
      .finally(() => setRefreshing(false));
  }, [regenerate, selectedId]);

  useEffect(() => {
    // Re-fetch the open detail after a full list refresh so stale content
    // never lingers.
    if (selectedId !== null && state === 'ready') loadDetail(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state]);

  return (
    <section>
      <h2>Agents</h2>
      <div className={styles.page}>
        <div className={styles.listPane}>
          {state === 'loading' && (
            <p className={styles.status} role="status">
              Loading results…
            </p>
          )}
          {state === 'error' && (
            <p role="alert">
              {error} <Button onClick={refresh}>Retry</Button>
            </p>
          )}
          {state === 'ready' && items.length === 0 && (
            <p className={styles.status}>No conversations yet.</p>
          )}
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              className={`${styles.row} ${selectedId === item.id ? styles.rowSelected : ''}`}
              onClick={() => onSelect(item.id)}
            >
              <span className={styles.rowTitle}>{item.title ?? 'Untitled'}</span>
              <span className={styles.rowSnippet}>
                {item.summary ? item.summary.snippet : 'No summary yet'}
              </span>
            </button>
          ))}
          {hasMore && (
            <Button variant="secondary" size="sm" onClick={loadOlder} disabled={loadingOlder}>
              {loadingOlder ? 'Loading…' : 'Load older'}
            </Button>
          )}
        </div>

        <div className={styles.detailPane}>
          {selectedId === null && (
            <p className={styles.status}>Select a session to see its summary.</p>
          )}
          {selectedId !== null && detailLoading && (
            <p className={styles.status} role="status">
              Loading summary…
            </p>
          )}
          {selectedId !== null && detailError !== null && <p role="alert">{detailError}</p>}
          {selectedId !== null && !detailLoading && detailError === null && detail === null && (
            <>
              <p className={styles.status}>No summary yet.</p>
              <Button onClick={onRefresh} disabled={refreshing}>
                {refreshing ? 'Generating…' : 'Generate summary'}
              </Button>
            </>
          )}
          {selectedId !== null && !detailLoading && detailError === null && detail !== null && (
            <>
              <div className={styles.detailHeader}>
                <h3>{detail.title}</h3>
                <Button onClick={onRefresh} disabled={refreshing} variant="secondary" size="sm">
                  {refreshing ? 'Regenerating…' : 'Refresh'}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  role="link"
                  onClick={() =>
                    navigate({ to: '/sessions/$sessionId', params: { sessionId: selectedId } })
                  }
                >
                  Open chat
                </Button>
              </div>
              <p className={styles.detailMeta}>
                {detail.model_name ?? 'unknown model'} ·{' '}
                {new Date(detail.generated_at).toLocaleString()}
              </p>
              <div className={styles.detailBody}>{detail.content}</div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
