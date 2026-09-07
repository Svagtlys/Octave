import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useWebSocket } from "./useWebSocket";

// Mock WebSocket
class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  url: string;
  readyState: number = 0;
  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
  }

  send(_data: string): void {}

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }
}

vi.stubGlobal("WebSocket", MockWebSocket);

describe("useWebSocket", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns initial connecting status", () => {
    const { result } = renderHook(() =>
      useWebSocket("ws://localhost:8000/ws"),
    );
    expect(result.current.status).toBe("connecting");
  });

  it("provides send function", () => {
    const { result } = renderHook(() =>
      useWebSocket("ws://localhost:8000/ws"),
    );
    expect(typeof result.current.send).toBe("function");
  });

  it("lastMessage is initially null", () => {
    const { result } = renderHook(() =>
      useWebSocket("ws://localhost:8000/ws"),
    );
    expect(result.current.lastMessage).toBeNull();
  });
});
