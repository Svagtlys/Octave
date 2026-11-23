"""SessionHub fan-out (issue #60): join/leave/broadcast with dead-socket
eviction. Fake websockets record sent frames; no real connections."""

import json

from octave.websocket.hub import SessionHub


class FakeWebSocket:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self._fail = fail

    async def send_text(self, text: str) -> None:
        if self._fail:
            raise RuntimeError("socket closed")
        self.sent.append(text)


async def test_broadcast_reaches_joined_sockets() -> None:
    hub = SessionHub()
    ws_a, ws_b = FakeWebSocket(), FakeWebSocket()
    hub.join("s_1", ws_a)
    hub.join("s_1", ws_b)
    hub.join("s_2", FakeWebSocket())
    await hub.broadcast("s_1", {"type": "event", "seq": 1})
    assert json.loads(ws_a.sent[0]) == {"type": "event", "seq": 1}
    assert json.loads(ws_b.sent[0]) == {"type": "event", "seq": 1}


async def test_leave_stops_delivery() -> None:
    hub = SessionHub()
    ws = FakeWebSocket()
    hub.join("s_1", ws)
    hub.leave("s_1", ws)
    await hub.broadcast("s_1", {"type": "event"})
    assert ws.sent == []


async def test_dead_socket_evicted_on_send_failure() -> None:
    hub = SessionHub()
    dead = FakeWebSocket(fail=True)
    hub.join("s_1", dead)
    await hub.broadcast("s_1", {"type": "event"})  # must not raise
    hub.join("s_1", FakeWebSocket())  # rejoin proves the set survived
    live = FakeWebSocket()
    hub.join("s_1", live)
    await hub.broadcast("s_1", {"type": "event"})
    assert len(live.sent) == 1


async def test_leave_unknown_session_is_noop() -> None:
    hub = SessionHub()
    hub.leave("nope", FakeWebSocket())
