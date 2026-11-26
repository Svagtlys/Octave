// Session transcript API surface (issue #43). Mirrors the backend contract
// in backend/src/octave/routes/sessions.py: cursor pagination by `seq`.
// Chat opens at the newest page (`end=true`) and walks backward via
// `before_seq`; both render items oldest-first within the page and carry
// `prev_cursor` (null = no older messages remain).
import { get, post, deleteRequest } from './client';

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

// --- session list / create / delete (issue #46) ---
// Mirrors backend routes/sessions.py: GET envelope {items,total,limit,offset},
// most-recently-active first; POST 201 with {title|null}; preview pre-truncated
// to 140 chars at the route boundary.

export interface SessionSummary {
  id: string;
  title: string | null;
  /** ISO-8601 datetime. */
  created_at: string;
  last_activity: string;
  /** Backend-truncated preview (<= 140 chars); null when no message events. */
  last_message: string | null;
}

export interface SessionListPage {
  items: SessionSummary[];
  total: number;
  limit: number;
  offset: number;
}

/** The caller's sessions, most-recently-active first. */
export async function fetchSessions(limit = 50, offset = 0): Promise<SessionListPage> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return get<SessionListPage>(`/api/sessions?${params}`);
}

/** Create a session; 201 -> the new row (last_activity = created_at). */
export async function createSession(title?: string): Promise<SessionSummary> {
  return post<SessionSummary>('/api/sessions', { title: title ?? null });
}

/**
 * Delete a session permanently (issue #160). Owner-only: 404 unknown id,
 * 403 exists-but-not-owner, 204 success (empty body). Child rows go via
 * FK cascade server-side.
 */
export async function deleteSession(sessionId: string): Promise<void> {
  await deleteRequest<void>(`/api/sessions/${encodeURIComponent(sessionId)}`);
}
