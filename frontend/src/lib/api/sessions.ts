// Session transcript API surface (issue #43). Mirrors the backend contract
// in backend/src/octave/routes/sessions.py: cursor pagination by `seq`.
// Chat opens at the newest page (`end=true`) and walks backward via
// `before_seq`; both render items oldest-first within the page and carry
// `prev_cursor` (null = no older messages remain).
import { get } from './client';

export interface TranscriptMessage {
  seq: number;
  kind: string;
  author: string | null;
  content: string | null;
  /** ISO-8601 datetime (events.created_at serialized). */
  timestamp: string;
  /** Dialect reasoning content (issue #45); present on agent rows when the
   * model emitted thinking. Rendered collapsed. */
  thinking?: string | null;
}

export interface TranscriptPage {
  items: TranscriptMessage[];
  next_cursor: number | null;
  prev_cursor: number | null;
}

/** The newest page of a session's transcript (chat-open). */
export async function fetchLatestMessages(sessionId: string): Promise<TranscriptPage> {
  return get<TranscriptPage>(`/api/sessions/${encodeURIComponent(sessionId)}/messages?end=true`);
}

/**
 * The page immediately older than `beforeSeq` (the previous page's
 * `prev_cursor`), oldest-first within the page. 401/403/404 raise
 * ApiError via the client.
 */
export async function fetchOlderMessages(
  sessionId: string,
  beforeSeq: number
): Promise<TranscriptPage> {
  const params = new URLSearchParams({ before_seq: String(beforeSeq) });
  return get<TranscriptPage>(`/api/sessions/${encodeURIComponent(sessionId)}/messages?${params}`);
}
