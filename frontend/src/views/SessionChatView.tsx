import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../lib/api/client';
import { fetchLatestMessages, fetchOlderMessages } from '../lib/api/sessions';
import type { TranscriptMessage } from '../lib/api/sessions';
import { MessageList } from '../components/chat/MessageList';
import { Button } from '../components/ui';
import styles from './SessionChatView.module.css';

type LoadState = 'loading' | 'ready' | 'error';

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) return "This session doesn't exist.";
    if (error.status === 403) return "You don't have access to this session.";
    return `Couldn't load messages (${error.status}).`;
  }
  return "Couldn't reach the server.";
}

export interface SessionChatViewProps {
  sessionId: string;
}

/**
 * Chat transcript view for one session (issue #43). Opens at the NEWEST
 * page and pages backward: "Load older messages" prepends earlier cursor
 * pages while the newest stay anchored. Loading, error (with retry), and
 * empty states are all rendered per the coding rules.
 */
export default function SessionChatView({ sessionId }: SessionChatViewProps) {
  const [state, setState] = useState<LoadState>('loading');
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [prevCursor, setPrevCursor] = useState<number | null>(null);
  const [prepending, setPrepending] = useState(false);
  // Guards against out-of-order responses and setState-after-unmount when
  // the sessionId changes mid-flight.
  const requestRef = useRef(0);

  // Newest page (mount / session switch / retry): replaces the list.
  const loadLatest = useCallback(() => {
    const requestId = ++requestRef.current;
    setState('loading');
    setError(null);
    fetchLatestMessages(sessionId)
      .then((page) => {
        if (requestRef.current !== requestId) return;
        setMessages(page.items);
        setPrevCursor(page.prev_cursor);
        setState('ready');
      })
      .catch((err: unknown) => {
        if (requestRef.current !== requestId) return;
        setError(describeError(err));
        setState('error');
      });
  }, [sessionId]);

  useEffect(() => {
    setMessages([]);
    setPrevCursor(null);
    loadLatest();
  }, [loadLatest]);

  // Older page: prepends above the current window; the newest messages
  // stay anchored at the bottom.
  const loadOlder = useCallback(async () => {
    if (prevCursor === null || prepending) return;
    setPrepending(true);
    const requestId = requestRef.current;
    try {
      const page = await fetchOlderMessages(sessionId, prevCursor);
      // sessionId changed (or a reload started) while the page was in
      // flight — dropping it keeps the list consistent with its cursor.
      if (requestRef.current !== requestId) return;
      setMessages((prev) => [...page.items, ...prev]);
      setPrevCursor(page.prev_cursor);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setPrepending(false);
    }
  }, [sessionId, prevCursor, prepending]);

  if (state === 'loading') {
    return (
      <p className={styles.status} role="status">
        Loading transcript…
      </p>
    );
  }

  if (state === 'error' && messages.length === 0) {
    return (
      <div className={styles.errorBox}>
        <p role="alert">{error}</p>
        <Button variant="secondary" size="sm" onClick={loadLatest}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className={styles.container}>
      {prevCursor !== null && (
        <div className={styles.loadMore}>
          <Button variant="ghost" size="sm" onClick={loadOlder} disabled={prepending}>
            {prepending ? 'Loading…' : 'Load older messages'}
          </Button>
        </div>
      )}
      {error && (
        <p className={styles.inlineError} role="alert">
          {error}
        </p>
      )}
      <MessageList messages={messages} />
    </div>
  );
}
