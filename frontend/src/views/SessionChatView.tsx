import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiError } from '../lib/api/client';
import { fetchSessionMessages } from '../lib/api/sessions';
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
 * Chat transcript view for one session (issue #43). Owns the fetch
 * lifecycle: first page on mount, "load newer" appends subsequent cursor
 * pages (the endpoint pages forward by `seq`). Loading, error (with
 * retry), and empty states are all rendered per the coding rules.
 */
export default function SessionChatView({ sessionId }: SessionChatViewProps) {
  const [state, setState] = useState<LoadState>('loading');
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [appending, setAppending] = useState(false);
  // Guards against out-of-order responses and setState-after-unmount when
  // the sessionId changes mid-flight.
  const requestRef = useRef(0);

  // First page (mount / session switch / retry): replaces the list wholesale.
  const loadFirstPage = useCallback(() => {
    const requestId = ++requestRef.current;
    setState('loading');
    setError(null);
    fetchSessionMessages(sessionId)
      .then((page) => {
        if (requestRef.current !== requestId) return;
        setMessages(page.items);
        setNextCursor(page.next_cursor);
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
    setNextCursor(null);
    loadFirstPage();
  }, [loadFirstPage]);

  const loadNewer = useCallback(async () => {
    if (nextCursor === null || appending) return;
    setAppending(true);
    const requestId = requestRef.current;
    try {
      const page = await fetchSessionMessages(sessionId, nextCursor);
      // sessionId changed (or a reload started) while the page was in
      // flight — dropping it keeps the list consistent with its cursor.
      if (requestRef.current !== requestId) return;
      setMessages((prev) => [...prev, ...page.items]);
      setNextCursor(page.next_cursor);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setAppending(false);
    }
  }, [sessionId, nextCursor, appending]);

  const retry = loadFirstPage;

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
        <Button variant="secondary" size="sm" onClick={retry}>
          Retry
        </Button>
      </div>
    );
  }

  return (
    <div className={styles.container}>
      {error && (
        <p className={styles.inlineError} role="alert">
          {error}
        </p>
      )}
      <MessageList messages={messages} />
      {nextCursor !== null && (
        <div className={styles.loadMore}>
          <Button variant="ghost" size="sm" onClick={loadNewer} disabled={appending}>
            {appending ? 'Loading…' : 'Load newer messages'}
          </Button>
        </div>
      )}
    </div>
  );
}
