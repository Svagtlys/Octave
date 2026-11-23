"""In-process session fan-out (issue #60).

Tracks joined websockets per session and broadcasts transcript frames.
Single-process by design: multi-worker deployments need a pub/sub
backplane (spec follow-up). Broadcast is best-effort — a dead socket is
evicted, never fails the transcript write that produced the event.
"""

import json
from typing import Any

from fastapi import FastAPI, WebSocket

__all__ = ["SessionHub", "get_hub"]


class SessionHub:
    """session_id -> joined websockets. Not thread-safe by need: every
    mutation happens on the event loop thread."""

    def __init__(self) -> None:
        self._sockets: dict[str, set[WebSocket]] = {}

    def join(self, session_id: str, websocket: WebSocket) -> None:
        self._sockets.setdefault(session_id, set()).add(websocket)

    def leave(self, session_id: str, websocket: WebSocket) -> None:
        sockets = self._sockets.get(session_id)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            self._sockets.pop(session_id, None)

    async def broadcast(self, session_id: str, payload: dict[str, Any]) -> None:
        for websocket in list(self._sockets.get(session_id, ())):
            try:
                await websocket.send_text(json.dumps(payload))
            except Exception:  # noqa: BLE001 — dead socket: evict, keep going
                self.leave(session_id, websocket)


def get_hub(app: FastAPI) -> SessionHub:
    """Lazily create the app-wide hub (the WS router has no lifespan)."""
    hub: SessionHub | None = getattr(app.state, "ws_hub", None)
    if hub is None:
        hub = SessionHub()
        app.state.ws_hub = hub
    return hub
