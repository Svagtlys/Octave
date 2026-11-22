import type { TranscriptMessage } from '../../lib/api/sessions';
import { Badge } from '../ui';
import styles from './MessageList.module.css';

export interface MessageListProps {
  messages: TranscriptMessage[];
}

/**
 * Transcript time-ordering helper: ascending `seq` (the endpoint's contract),
 * defensive sort so a mis-ordered page never renders out of order.
 */
function sortBySeq(messages: TranscriptMessage[]): TranscriptMessage[] {
  return [...messages].sort((a, b) => a.seq - b.seq);
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatFullTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString([], {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** Up-to-two-character initials from a participant label; fallback marker. */
function initials(author: string | null): string {
  if (!author) return '?';
  const parts = author.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/**
 * Read-only chat transcript (issue #43): one row per message with avatar,
 * author, explicit role badge (You / Agent), timestamp, and content.
 * `kind` from the backend is verbatim (`user_message` / `assistant_message`);
 * anything unexpected degrades to the neutral (user) rendering rather than
 * crashing — the endpoint is the filter authority.
 */
export function MessageList({ messages }: MessageListProps) {
  if (messages.length === 0) {
    return (
      <p className={styles.empty} role="status">
        No messages yet — the conversation will appear here.
      </p>
    );
  }

  return (
    <ol className={styles.list} aria-label="Chat messages">
      {sortBySeq(messages).map((message) => {
        const isAgent = message.kind === 'assistant_message';
        const time = formatTime(message.timestamp);
        return (
          <li
            key={message.seq}
            className={[styles.row, isAgent ? styles.agentRow : styles.userRow].join(' ')}
          >
            <span
              className={[styles.avatar, isAgent ? styles.avatarAgent : styles.avatarUser].join(
                ' '
              )}
              aria-hidden="true"
            >
              {initials(message.author)}
            </span>
            <div className={styles.body}>
              <div className={styles.header}>
                <span className={styles.author}>{message.author ?? 'Unknown'}</span>
                <Badge tone={isAgent ? 'accent' : 'neutral'} className={styles.roleBadge}>
                  {isAgent ? 'Agent' : 'You'}
                </Badge>
                {time && (
                  <time
                    className={styles.timestamp}
                    dateTime={message.timestamp}
                    title={formatFullTimestamp(message.timestamp)}
                  >
                    {time}
                  </time>
                )}
              </div>
              <div className={styles.content}>
                {message.content ?? (
                  <span className={styles.corrupt}>[unavailable message content]</span>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
