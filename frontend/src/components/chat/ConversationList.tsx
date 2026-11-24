// Conversation list panel (issue #46). Presentational: data + callbacks come
// from useConversations via props, so the same component serves the chat-view
// panel and the /sessions hub. Delete confirmation is a local concern (which
// row is pending) and lives here; the actual removal is the callback's job.
import { useState } from 'react';
import { Button, Modal } from '../ui';
import type { SessionSummary } from '../../lib/api/sessions';
import type { UseConversations } from '../../lib/api/useConversations';
import styles from './ConversationList.module.css';

export interface ConversationListProps {
  conversations: UseConversations;
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onConfirmDelete: (id: string) => void;
}

const MINUTE = 60_000;
const HOUR = 3_600_000;
const DAY = 86_400_000;

/** Relative activity label: just now / 5m / 3h / 2d. `now` is injectable for tests. */
export function formatRelativeTime(iso: string, now: number = Date.now()): string {
  const delta = Math.max(0, now - Date.parse(iso));
  if (delta < MINUTE) return 'just now';
  if (delta < HOUR) return `${Math.floor(delta / MINUTE)}m`;
  if (delta < DAY) return `${Math.floor(delta / HOUR)}h`;
  return `${Math.floor(delta / DAY)}d`;
}

function itemLabel(session: SessionSummary): string {
  const title = session.title?.trim();
  if (title) return title;
  const firstLine = session.last_message?.split('\n')[0]?.trim();
  return firstLine || 'New conversation';
}

function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M3 6h18M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2m2 0v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function ConversationList({
  conversations,
  activeId,
  onSelect,
  onNew,
  onConfirmDelete,
}: ConversationListProps) {
  const { sessions, state, error, hasMore, loadingOlder, refresh, loadOlder } = conversations;
  const [pendingDelete, setPendingDelete] = useState<SessionSummary | null>(null);
  const [creating, setCreating] = useState(false);

  const handleNew = () => {
    setCreating(true);
    try {
      onNew();
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <span className={styles.headerTitle}>Conversations</span>
        <Button size="sm" variant="secondary" onClick={handleNew} disabled={creating}>
          + New chat
        </Button>
      </div>

      {error && (
        <p className={styles.inlineError} role="alert">
          {error}
        </p>
      )}

      {state === 'loading' && (
        <p className={styles.status} role="status">
          Loading conversations…
        </p>
      )}

      {state === 'error' && sessions.length === 0 && (
        <Button size="sm" variant="secondary" onClick={refresh}>
          Retry
        </Button>
      )}

      {state === 'ready' && sessions.length === 0 && (
        <p className={styles.empty}>No conversations yet. Start one!</p>
      )}

      {sessions.length > 0 && (
        <ul className={styles.list}>
          {sessions.map((session) => {
            const active = session.id === activeId;
            return (
              <li key={session.id} className={styles.item}>
                <button
                  type="button"
                  className={active ? styles.itemButtonActive : styles.itemButton}
                  aria-current={active ? 'true' : undefined}
                  aria-label={itemLabel(session)}
                  onClick={() => onSelect(session.id)}
                >
                  <span className={styles.itemLabel}>{itemLabel(session)}</span>
                  <span className={styles.itemMeta}>
                    {session.last_message && (
                      <span className={styles.itemPreview}>{session.last_message}</span>
                    )}
                    <span className={styles.itemTime}>
                      {formatRelativeTime(session.last_activity)}
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  className={styles.deleteButton}
                  aria-label={`Delete conversation ${itemLabel(session)}`}
                  onClick={() => setPendingDelete(session)}
                >
                  <TrashIcon />
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {hasMore && (
        <Button size="sm" variant="ghost" onClick={loadOlder} disabled={loadingOlder}>
          {loadingOlder ? 'Loading…' : 'Load older conversations'}
        </Button>
      )}

      <Modal
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title="Delete conversation?"
        description="This can't be undone."
        footer={
          <>
            <Button variant="secondary" size="sm" onClick={() => setPendingDelete(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              size="sm"
              onClick={() => {
                if (pendingDelete) onConfirmDelete(pendingDelete.id);
                setPendingDelete(null);
              }}
            >
              Delete
            </Button>
          </>
        }
      />
    </div>
  );
}
