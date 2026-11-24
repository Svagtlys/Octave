import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Session-scoped WebSocket transport (issue #44). Owns connection lifecycle
 * only — open, auto-join (mount + every reconnect), typed frame send/receive,
 * status, capped exponential-backoff reconnect, and a stale-session guard.
 * Transcript merging lives in the consuming view, not here.
 *
 * Frame contract (backend octave/websocket/connection.py, issue #60):
 *   out: {type:'join', session_id} | {type:'user_message', session_id, content}
 *   in:  {type:'event', session_id, seq, kind, author, content}
 *        {type:'error', error}   (no session_id — always surfaced)
 *        {type:'stream_thinking', session_id, round, delta}
 *        {type:'stream_text', session_id, round, delta}
 *        {type:'stream_tool_start', session_id, round, index, call_id, name}
 *        {type:'stream_tool_args', session_id, round, index, arguments}
 *        {type:'stream_tool_result', session_id, round, call_id, name, content, is_error}
 */

export type SocketStatus = 'connecting' | 'open' | 'closed' | 'error';

export interface SocketEvent {
  type: 'event';
  session_id: string;
  seq: number;
  kind: string;
  author: string | null;
  content: string | null;
}

export interface SocketError {
  type: 'error';
  error: string;
}

export interface StreamThinkingFrame {
  type: 'stream_thinking';
  session_id: string;
  round: number;
  delta: string;
}
export interface StreamTextFrame {
  type: 'stream_text';
  session_id: string;
  round: number;
  delta: string;
}
export interface StreamToolStartFrame {
  type: 'stream_tool_start';
  session_id: string;
  round: number;
  index: number;
  call_id: string;
  name: string;
}
export interface StreamToolArgsFrame {
  type: 'stream_tool_args';
  session_id: string;
  round: number;
  index: number;
  arguments: string;
}
export interface StreamToolResultFrame {
  type: 'stream_tool_result';
  session_id: string;
  round: number;
  call_id: string;
  name: string;
  content: string;
  is_error: boolean;
}

export type SocketFrame =
  | SocketEvent
  | SocketError
  | StreamThinkingFrame
  | StreamTextFrame
  | StreamToolStartFrame
  | StreamToolArgsFrame
  | StreamToolResultFrame;

const STREAM_TYPES = new Set([
  'stream_thinking',
  'stream_text',
  'stream_tool_start',
  'stream_tool_args',
  'stream_tool_result',
]);

/** Frame sink installed by the consumer; called for every matching frame. */
export type FrameSink = (frame: SocketFrame) => void;

export interface UseSessionSocket {
  status: SocketStatus;
  /** Sends {"type":"user_message","session_id","content"}; no-op unless open. */
  sendUserMessage: (content: string) => void;
}

function getWsUrl(): string {
  const apiUrl = import.meta.env.VITE_API_URL || 'http://localhost:8000';
  const wsProtocol = apiUrl.startsWith('https') ? 'wss' : 'ws';
  const host = apiUrl.replace(/^https?:\/\//, '');
  return `${wsProtocol}://${host}/ws`;
}

export function useSessionSocket(
  sessionId: string,
  onFrameRef: { current: FrameSink | null }
): UseSessionSocket {
  const [status, setStatus] = useState<SocketStatus>('connecting');
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const backoffRef = useRef(1000);

  useEffect(() => {
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      setStatus('connecting');
      const ws = new WebSocket(getWsUrl());

      ws.onopen = () => {
        if (disposed) return;
        setStatus('open');
        backoffRef.current = 1000;
        ws.send(JSON.stringify({ type: 'join', session_id: sessionId }));
      };

      ws.onmessage = (event: MessageEvent) => {
        if (disposed) return;
        let frame: unknown;
        try {
          frame = JSON.parse(String(event.data));
        } catch {
          return; // Malformed frame at the transport edge — nothing to route.
        }
        const type = (frame as { type?: unknown }).type;
        const isStream = typeof type === 'string' && STREAM_TYPES.has(type);
        if (type !== 'event' && type !== 'error' && !isStream) return; // joined/pong/unknown
        const candidate = frame as
          | SocketEvent
          | (StreamThinkingFrame & { session_id: string })
          | (SocketError & { session_id?: string });
        if (type !== 'error' && (candidate as { session_id?: string }).session_id !== sessionId) {
          return; // Stale frame from a session we no longer view.
        }
        onFrameRef.current?.(candidate as SocketFrame);
      };

      ws.onerror = () => {
        if (!disposed) setStatus('error');
      };

      ws.onclose = () => {
        if (disposed) return;
        setStatus('closed');
        reconnectTimerRef.current = setTimeout(connect, Math.min(backoffRef.current, 8000));
        backoffRef.current *= 2;
      };

      wsRef.current = ws;
    };

    connect();

    return () => {
      disposed = true;
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [sessionId, onFrameRef]);

  const sendUserMessage = useCallback(
    (content: string) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(
          JSON.stringify({ type: 'user_message', session_id: sessionId, content })
        );
      }
    },
    [sessionId]
  );

  return { status, sendUserMessage };
}
