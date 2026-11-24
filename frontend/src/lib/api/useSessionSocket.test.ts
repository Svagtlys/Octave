import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useRef } from 'react';
import { useSessionSocket, type SocketFrame } from './useSessionSocket';

/**
 * Fake WebSocket: instances are recorded; open()/receive()/serverClose() drive
 * the handlers from tests. readyState mirrors the real constants (0/1/3).
 */
class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = FakeWebSocket.CLOSED;
  }
  // Test drivers
  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }
  receive(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
  receiveRaw(data: string) {
    this.onmessage?.({ data });
  }
  serverClose() {
    this.readyState = FakeWebSocket.CLOSED;
    this.onclose?.();
  }
  lastSent() {
    return JSON.parse(this.sent[this.sent.length - 1]);
  }
}

function eventFrame(overrides: Record<string, unknown> = {}) {
  return {
    type: 'event',
    session_id: 's_1',
    seq: 5,
    kind: 'assistant_message',
    author: 'Octave',
    content: 'Hi',
    ...overrides,
  };
}

describe('useSessionSocket', () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeWebSocket as unknown as typeof WebSocket);
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  function setup(sessionId = 's_1') {
    const frames: SocketFrame[] = [];
    const { result, rerender } = renderHook(
      ({ sid }: { sid: string }) => {
        const sinkRef = useRef<(frame: SocketFrame) => void>(() => {});
        const socket = useSessionSocket(sid, sinkRef);
        return { socket, sinkRef };
      },
      { initialProps: { sid: sessionId } }
    );
    // Capture the sink the hook installed; frames flow through it in tests.
    const handler = (frame: SocketFrame) => frames.push(frame);
    act(() => {
      result.current.sinkRef.current = handler;
    });
    return { frames, rerender, socket: () => result.current.socket };
  }

  it('joins the session as soon as the socket opens', () => {
    setup();
    const ws = FakeWebSocket.instances[0];
    ws.open();
    expect(ws.lastSent()).toEqual({ type: 'join', session_id: 's_1' });
  });

  it('sendUserMessage sends a user_message frame while open', () => {
    const { socket } = setup();
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    act(() => socket().sendUserMessage('hello world'));
    expect(ws.lastSent()).toEqual({
      type: 'user_message',
      session_id: 's_1',
      content: 'hello world',
    });
  });

  it('sendUserMessage is a no-op while not open', () => {
    const { socket } = setup();
    const ws = FakeWebSocket.instances[0];
    act(() => socket().sendUserMessage('too early'));
    expect(ws.sent).toEqual([]);
  });

  it('surfaces event and error frames to the sink; ignores joined/pong/malformed', () => {
    const { frames } = setup();
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    act(() => ws.receive(eventFrame()));
    act(() => ws.receive({ type: 'error', error: 'boom' }));
    act(() => ws.receive({ type: 'joined', session_id: 's_1' }));
    act(() => ws.receive({ type: 'pong', payload: '' }));
    act(() => ws.receiveRaw('not json'));
    expect(frames).toHaveLength(2);
    expect(frames[0]).toMatchObject({ type: 'event', seq: 5 });
    expect(frames[1]).toEqual({ type: 'error', error: 'boom' });
  });

  it('drops event frames for a different session', () => {
    const { frames } = setup();
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    act(() => ws.receive(eventFrame({ session_id: 's_other' })));
    expect(frames).toHaveLength(0);
  });

  it('reports closed on server close, then reconnects with backoff and re-joins', () => {
    setup();
    const ws1 = FakeWebSocket.instances[0];
    act(() => ws1.open());
    act(() => ws1.serverClose());
    expect(FakeWebSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1000);
    expect(FakeWebSocket.instances).toHaveLength(2);
    const ws2 = FakeWebSocket.instances[1];
    ws2.open();
    expect(ws2.lastSent()).toEqual({ type: 'join', session_id: 's_1' });
  });

  it('reopens and re-joins when the sessionId changes; stale frames drop', () => {
    const { frames, rerender } = setup();
    const ws1 = FakeWebSocket.instances[0];
    act(() => ws1.open());
    rerender({ sid: 's_2' });
    expect(ws1.readyState).toBe(FakeWebSocket.CLOSED);
    const ws2 = FakeWebSocket.instances[1];
    act(() => ws2.open());
    expect(ws2.lastSent()).toEqual({ type: 'join', session_id: 's_2' });
    act(() => ws2.receive(eventFrame({ session_id: 's_1' })));
    expect(frames).toHaveLength(0);
    act(() => ws2.receive(eventFrame({ session_id: 's_2' })));
    expect(frames).toHaveLength(1);
  });

  it('surfaces stream_* frames to the sink', () => {
    const { frames } = setup();
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    act(() => ws.receive({ type: 'stream_thinking', session_id: 's_1', round: 0, delta: 'hmm' }));
    act(() => ws.receive({ type: 'stream_text', session_id: 's_1', round: 0, delta: 'Hel' }));
    act(() =>
      ws.receive({
        type: 'stream_tool_start',
        session_id: 's_1',
        round: 0,
        index: 0,
        call_id: 'c1',
        name: 'read',
      })
    );
    act(() =>
      ws.receive({
        type: 'stream_tool_args',
        session_id: 's_1',
        round: 0,
        index: 0,
        arguments: '{"a"',
      })
    );
    act(() =>
      ws.receive({
        type: 'stream_tool_result',
        session_id: 's_1',
        round: 0,
        call_id: 'c1',
        name: 'read',
        content: 'ok',
        is_error: false,
      })
    );
    expect(frames).toHaveLength(5);
    expect(frames[0]).toMatchObject({ type: 'stream_thinking', delta: 'hmm' });
    expect(frames[4]).toMatchObject({ type: 'stream_tool_result', call_id: 'c1' });
  });

  it('drops stream frames for a different session', () => {
    const { frames } = setup();
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    act(() => ws.receive({ type: 'stream_text', session_id: 's_other', round: 0, delta: 'x' }));
    expect(frames).toHaveLength(0);
  });

  it('unmount closes the socket and cancels reconnects', () => {
    const { unmount } = renderHook(() => {
      const sinkRef = useRef<(frame: SocketFrame) => void>(() => {});
      return useSessionSocket('s_9', sinkRef);
    });
    const ws = FakeWebSocket.instances[0];
    act(() => ws.open());
    act(() => ws.serverClose()); // schedules a backoff reconnect
    unmount();
    expect(ws.readyState).toBe(FakeWebSocket.CLOSED);
    vi.advanceTimersByTime(30_000);
    // No further sockets were created after unmount.
    expect(FakeWebSocket.instances).toHaveLength(1);
  });
});
