import { useState, useRef, useCallback, useEffect } from "react";

export type WebSocketStatus = "connecting" | "open" | "closed" | "error";

interface UseWebSocketReturn {
  status: WebSocketStatus;
  send: (message: string) => void;
  lastMessage: string | null;
}

function getWsUrl(url?: string): string {
  if (url) return url;
  const apiUrl = import.meta.env.VITE_API_URL || "http://localhost:8000";
  const wsProtocol = apiUrl.startsWith("https") ? "wss" : "ws";
  const host = apiUrl.replace(/^https?:\/\//, "");
  return `${wsProtocol}://${host}/ws`;
}

export function useWebSocket(url?: string): UseWebSocketReturn {
  const [status, setStatus] = useState<WebSocketStatus>("connecting");
  const [lastMessage, setLastMessage] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const backoffRef = useRef<number>(1000);

  const connect = useCallback(() => {
    const wsUrl = getWsUrl(url);
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      setStatus("open");
      backoffRef.current = 1000;
    };

    ws.onmessage = (event) => {
      setLastMessage(event.data);
    };

    ws.onerror = () => {
      setStatus("error");
    };

    ws.onclose = () => {
      setStatus("closed");
      // Reconnect with exponential backoff
      const delay = Math.min(backoffRef.current, 8000);
      backoffRef.current *= 2;
      reconnectTimeoutRef.current = setTimeout(connect, delay);
    };

    wsRef.current = ws;
  }, [url]);

  const send = useCallback(
    (message: string) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(message);
      }
    },
    [],
  );

  useEffect(() => {
    setStatus("connecting");
    connect();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      wsRef.current?.close();
    };
  }, [connect]);

  return { status, send, lastMessage };
}
