// Session-summary API surface (issue #112). Mirrors the backend contract in
// backend/src/octave/routes/summaries.py: list = caller's sessions with the
// cached summary snippet attached (or null); detail = peek (never triggers
// inference); refresh = collect(force=True) via the LLM.
import { get, post } from './client';

export interface SummarySnippet {
  title: string;
  /** Route-truncated body (<= 140 chars). */
  snippet: string;
  model_name: string | null;
  /** ISO-8601 datetime. */
  generated_at: string;
}

export interface SummaryDetail {
  title: string;
  content: string;
  model_name: string | null;
  generated_at: string;
}

export interface SessionSummaryListItem {
  id: string;
  title: string | null;
  created_at: string;
  last_activity: string;
  last_message: string | null;
  summary: SummarySnippet | null;
}

export interface SessionSummaryListPage {
  items: SessionSummaryListItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface SessionSummaryDetailResponse {
  session_id: string;
  summary: SummaryDetail | null;
}

export async function fetchSessionSummaries(
  limit = 20,
  offset = 0
): Promise<SessionSummaryListPage> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  return get<SessionSummaryListPage>(`/api/session-summaries?${params}`);
}

export async function fetchSessionSummaryDetail(
  sessionId: string
): Promise<SessionSummaryDetailResponse> {
  return get<SessionSummaryDetailResponse>(
    `/api/session-summaries/${encodeURIComponent(sessionId)}`
  );
}

export async function refreshSessionSummary(
  sessionId: string
): Promise<SessionSummaryDetailResponse> {
  return post<SessionSummaryDetailResponse>(
    `/api/session-summaries/${encodeURIComponent(sessionId)}/refresh`
  );
}
