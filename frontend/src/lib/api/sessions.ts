// Session transcript API surface (issue #43). Mirrors the backend contract
// in backend/src/octave/routes/sessions.py: forward-only cursor pagination
// by `seq` (next_cursor null terminates), kinds filtered server-side to
// user_message / assistant_message.
import { get } from './client';

export interface TranscriptMessage {
  seq: number;
  kind: string;
  author: string | null;
  content: string | null;
  /** ISO-8601 datetime (events.created_at serialized). */
  timestamp: string;
}

export interface TranscriptPage {
  items: TranscriptMessage[];
  next_cursor: number | null;
}

/**
 * One page of a session's transcript, oldest-first.
 * Omit `cursor` for the first page; pass the previous page's
 * `next_cursor` to advance. 401/403/404 raise ApiError via the client.
 */
export async function fetchSessionMessages(
  sessionId: string,
  cursor?: number
): Promise<TranscriptPage> {
  const params = new URLSearchParams();
  if (cursor !== undefined) params.set('cursor', String(cursor));
  const qs = params.toString();
  const path = `/api/sessions/${encodeURIComponent(sessionId)}/messages`;
  return get<TranscriptPage>(qs ? `${path}?${qs}` : path);
}
