import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from '@tanstack/react-router';
import { ApiError } from '../lib/api/client';
import { fetchLatestMessages, fetchOlderMessages } from '../lib/api/sessions';
import type { TranscriptMessage } from '../lib/api/sessions';
import { useSessionSocket, type FrameSink, type SocketFrame } from '../lib/api/useSessionSocket';
import { MessageList } from '../components/chat/MessageList';
import { ChatInput } from '../components/chat/ChatInput';
import { ToolCallCard } from '../components/chat/ToolCallCard';
import { ConversationList } from '../components/chat/ConversationList';
import { useConversations } from '../lib/api/useConversations';
import { Button } from '../components/ui';
import styles from './SessionChatView.module.css';

type LoadState = 'loading' | 'ready' | 'error';

interface ToolCardState {
  callId: string;
  name: string;
  round: number;
  index: number;
  arguments: string;
  content: string | null;
  is_error: boolean;
  done: boolean;
}

interface ProvisionalState {
  thinking: string;
  text: string;
  cards: ToolCardState[];
}

const EMPTY_PROVISIONAL: ProvisionalState = { thinking: '', text: '', cards: [] };

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
 * Chat transcript view with a live composer (issues #43 + #44). Opens at the
 * NEWEST page and pages backward; sends go over the session WebSocket and
 * render via the hub echo (every rendered row carries a server seq — no
 * optimistic state). "Agent is thinking…" shows between send and reply.
 */
export default function SessionChatView({ sessionId }: SessionChatViewProps) {
  const [state, setState] = useState<LoadState>('loading');
  const [error, setError] = useState<string | null>(null);
  const [messages, setMessages] = useState<TranscriptMessage[]>([]);
  const [prevCursor, setPrevCursor] = useState<number | null>(null);
  const [prepending, setPrepending] = useState(false);
  const [awaitingReply, setAwaitingReply] = useState(false);
  const [provisional, setProvisional] = useState<ProvisionalState>(EMPTY_PROVISIONAL);
  // Guards against out-of-order responses and setState-after-unmount when
  // the sessionId changes mid-flight.
  const requestRef = useRef(0);
  // Ref mirror of the transcript so the WS frame sink never sees a stale
  // closure (the hook installs the sink once per connection).
  const messagesRef = useRef<TranscriptMessage[]>([]);

  const applyMessages = useCallback(
    (updater: (prev: TranscriptMessage[]) => TranscriptMessage[]) => {
      messagesRef.current = updater(messagesRef.current);
      setMessages(messagesRef.current);
    },
    []
  );

  // Newest page (mount / session switch / retry / reconnect): replaces the list.
  const loadLatest = useCallback(() => {
    const requestId = ++requestRef.current;
    setState('loading');
    setError(null);
    fetchLatestMessages(sessionId)
      .then((page) => {
        if (requestRef.current !== requestId) return;
        applyMessages(() => page.items);
        setPrevCursor(page.prev_cursor);
        setState('ready');
      })
      .catch((err: unknown) => {
        if (requestRef.current !== requestId) return;
        setError(describeError(err));
        setState('error');
      });
  }, [sessionId, applyMessages]);

  useEffect(() => {
    messagesRef.current = [];
    setMessages([]);
    setPrevCursor(null);
    setAwaitingReply(false);
    setProvisional(EMPTY_PROVISIONAL);
    hasOpenedRef.current = false;
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
      applyMessages((prev) => [...page.items, ...prev]);
      setPrevCursor(page.prev_cursor);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setPrepending(false);
    }
  }, [sessionId, prevCursor, prepending, applyMessages]);

  // Latest-callback pattern: the sink lives in a ref the hook reads per
  // frame, so re-renders never re-open the socket.
  const handleFrame = useCallback(
    (frame: SocketFrame) => {
      if (frame.type === 'error') {
        setError(frame.error || 'The agent run failed.');
        setAwaitingReply(false);
        setProvisional(EMPTY_PROVISIONAL);
        return;
      }
      if (frame.type === 'stream_thinking') {
        setProvisional((p) => ({ ...p, thinking: p.thinking + frame.delta }));
        return;
      }
      if (frame.type === 'stream_text') {
        setProvisional((p) => ({ ...p, text: p.text + frame.delta }));
        return;
      }
      if (frame.type === 'stream_tool_start') {
        setProvisional((p) => ({
          ...p,
          cards: [
            ...p.cards,
            {
              callId: frame.call_id,
              name: frame.name,
              round: frame.round,
              index: frame.index,
              arguments: '',
              content: null,
              is_error: false,
              done: false,
            },
          ],
        }));
        return;
      }
      if (frame.type === 'stream_tool_args') {
        setProvisional((p) => ({
          ...p,
          cards: p.cards.map((card) =>
            card.round === frame.round && card.index === frame.index
              ? { ...card, arguments: card.arguments + frame.arguments }
              : card
          ),
        }));
        return;
      }
      if (frame.type === 'stream_tool_result') {
        setProvisional((p) => ({
          ...p,
          cards: p.cards.map((card) =>
            card.callId === frame.call_id
              ? { ...card, content: frame.content, is_error: frame.is_error, done: true }
              : card
          ),
        }));
        return;
      }
      if (frame.type !== 'event') return;
      const isChat = frame.kind === 'user_message' || frame.kind === 'assistant_message';
      if (!isChat) return; // tool/system frames are not transcript rows.
      if (frame.kind === 'assistant_message') {
        setAwaitingReply(false);
        setProvisional((p) => ({ ...p, thinking: '', text: '' })); // reconcile: committed row wins (Decision 5)
      }
      if (messagesRef.current.some((m) => m.seq === frame.seq)) return; // dedupe
      applyMessages((prev) =>
        [
          ...prev,
          {
            seq: frame.seq,
            kind: frame.kind,
            author: frame.author,
            content: frame.content,
            // Echo frames carry no timestamp (design Decision 6); reload
            // restores authoritative values from the transcript API.
            timestamp: new Date().toISOString(),
          },
        ].sort((a, b) => a.seq - b.seq)
      );
    },
    [applyMessages]
  );
  const frameSinkRef = useRef<FrameSink | null>(null);
  useEffect(() => {
    frameSinkRef.current = handleFrame;
  }, [handleFrame]);

  const { status, sendUserMessage } = useSessionSocket(sessionId, frameSinkRef);

  // Reconnect resync (Decision 7): a non-open -> open transition after the
  // first open re-fetches the newest page to recover events missed offline.
  const prevStatusRef = useRef(status);
  const hasOpenedRef = useRef(false);
  useEffect(() => {
    const was = prevStatusRef.current;
    prevStatusRef.current = status;
    if (status === 'open') {
      if (hasOpenedRef.current && was !== 'open') loadLatest();
      hasOpenedRef.current = true;
    }
  }, [status, loadLatest]);

  const handleSend = useCallback(
    (text: string) => {
      setError(null);
      sendUserMessage(text);
      setProvisional(EMPTY_PROVISIONAL);
      setAwaitingReply(true);
    },
    [sendUserMessage]
  );

  // Conversation-history panel (issue #46): the list survives the
  // transcript's loading/error states, so the early returns moved inside
  // the chat column below.
  const navigate = useNavigate();
  const conversations = useConversations();

  const handleSelect = useCallback(
    (id: string) => {
      if (id !== sessionId) {
        void navigate({ to: '/sessions/$sessionId', params: { sessionId: id } });
      }
    },
    [navigate, sessionId]
  );

  const handleNew = useCallback(() => {
    void conversations.create().then((id) => {
      if (id) void navigate({ to: '/sessions/$sessionId', params: { sessionId: id } });
    });
  }, [conversations, navigate]);

  const handleConfirmDelete = useCallback(
    async (id: string) => {
      try {
        const next = await conversations.remove(id);
        // Only the open conversation navigates; deleting another row is a
        // pure list edit. remove() throws on failure, so we never navigate
        // a failed delete.
        if (id === sessionId) {
          void navigate(
            next
              ? { to: '/sessions/$sessionId', params: { sessionId: next.id } }
              : { to: '/sessions' }
          );
        }
      } catch {
        // Inline error already set by the hook.
      }
    },
    [conversations, navigate, sessionId]
  );

  return (
    <div className={styles.layout}>
      <div className={styles.panelColumn}>
        <ConversationList
          conversations={conversations}
          activeId={sessionId}
          onSelect={handleSelect}
          onNew={handleNew}
          onConfirmDelete={(id) => void handleConfirmDelete(id)}
        />
      </div>
      <div className={styles.chatColumn}>
        {state === 'loading' && (
          <p className={styles.status} role="status">
            Loading transcript…
          </p>
        )}
        {state === 'error' && messages.length === 0 && (
          <div className={styles.errorBox}>
            <p role="alert">{error}</p>
            <Button variant="secondary" size="sm" onClick={loadLatest}>
              Retry
            </Button>
          </div>
        )}
        {state === 'ready' && (
          <>
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
            {awaitingReply && !provisional.thinking && !provisional.text && (
              <p className={styles.thinking} role="status">
                Agent is thinking…
              </p>
            )}
            {(provisional.thinking || provisional.text) && (
              <div className={styles.streaming} role="status" aria-live="polite">
                {provisional.thinking && (
                  <details className={styles.thinkingBlock} open={!provisional.text}>
                    <summary>Thinking…</summary>
                    <div className={styles.thinkingContent}>{provisional.thinking}</div>
                  </details>
                )}
                <div className={styles.streamingText}>{provisional.text}</div>
              </div>
            )}
            {provisional.cards.length > 0 && (
              <div className={styles.toolCards}>
                {provisional.cards.map((card) => (
                  <ToolCallCard
                    key={card.callId}
                    name={card.name}
                    status={card.done ? (card.is_error ? 'error' : 'done') : 'calling'}
                    arguments={card.arguments}
                    output={card.content}
                  />
                ))}
              </div>
            )}
            <ChatInput onSend={handleSend} disabled={status !== 'open'} />
            {status !== 'open' && (
              <p className={styles.disconnected} role="status">
                Disconnected — retrying…
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
