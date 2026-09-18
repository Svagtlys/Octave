"""MCP server lifecycle manager — supervision, auto-restart, health.

Role (design spec): the manager owns one ``McpClient`` per registered server
plus a supervisor task that is the **sole caller** of that client's lifecycle
methods — ``McpClient.restart()``'s single-caller contract enforced by
construction. Consumers read state via ``status()``/``status_of()`` and call
tools through ``get_client()``; restarts are transparent (same client
instance, config retained client-side).

Never imports the ``mcp`` SDK: clients are built through ``client_factory``
(see the quarantine rule in ``client.py``). ``start_all``/``stop_all`` must be
called from the same task (the lifespan task) — the task group is entered
manually so it outlives the call.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import anyio
from anyio.abc import TaskGroup

from octave.mcp.client import McpClient
from octave.mcp.config import McpSettings, ServerConfig, StdioConfig
from octave.mcp.errors import McpConfigError, McpError

__all__ = ["ClientFactory", "McpServerManager", "ServerState", "ServerStatus"]

logger = logging.getLogger(__name__)

ServerState = Literal["stopped", "starting", "connected", "restarting", "crashed"]
"""Server lifecycle states (spec state machine)."""


@dataclass(frozen=True)
class ServerStatus:
    """Point-in-time snapshot of one managed server."""

    id: str
    name: str
    state: ServerState
    transport: str
    """``"stdio" | "http"`` — display metadata derived from the config type."""

    restart_count: int
    """Successful auto-restart cycles since registration."""

    consecutive_failures: int
    """Current backoff streak; reset by stabilization or manual restart."""

    last_error: str | None
    """Most recent failure reason (Octave exception message — already redacted)."""

    last_state_change: datetime


ClientFactory = Callable[[Callable[[str], None]], McpClient]
"""Builds a client wired to the manager's ``on_lost(reason)`` hook."""


@dataclass
class _ManagedServer:
    """Internal bookkeeping for one registered server."""

    id: str
    name: str
    config: ServerConfig
    client: McpClient
    state: ServerState = "stopped"
    restart_count: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    last_state_change: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    wake: anyio.Event = field(default_factory=anyio.Event)
    reason: str | None = None
    """Latest on_lost signal: ``"death"`` wins over ``"timeout"``."""

    cmd_restart: bool = False
    scope: anyio.CancelScope | None = None
    stable_deadline: float = 0.0
    """anyio clock time after which the failure counter may reset."""


def _default_client_factory(settings: McpSettings) -> ClientFactory:
    def build(on_lost: Callable[[str], None]) -> McpClient:
        return McpClient(settings=settings, on_lost=on_lost)

    return build


class McpServerManager:
    """Registry + supervisor for a fleet of MCP server connections."""

    def __init__(
        self,
        *,
        settings: McpSettings | None = None,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._settings = settings or McpSettings()
        self._client_factory = client_factory or _default_client_factory(
            self._settings
        )
        self._servers: dict[str, _ManagedServer] = {}
        self._task_group: TaskGroup | None = None

    def register(self, *, id: str, name: str, config: ServerConfig) -> None:
        """Add a server. Allowed only before ``start_all()``.

        Raises ``McpConfigError`` on duplicate id, ``McpError`` after start.
        """
        if self._task_group is not None:
            raise McpError("cannot register after start_all()")
        if id in self._servers:
            raise McpConfigError(f"duplicate server id: {id}")
        self._servers[id] = _ManagedServer(
            id=id,
            name=name,
            config=config,
            client=self._client_factory(
                lambda reason, _id=id: self._on_lost(_id, reason)
            ),
        )

    def _on_lost(self, id: str, reason: str) -> None:
        """Client hook: record the signal, wake the supervisor.

        Synchronous; safe to call from any task (SDK receive loop or caller).
        """
        rec = self._servers[id]
        if reason == "death" or rec.reason is None:
            rec.reason = reason
        rec.wake.set()

    def get_client(self, id: str) -> McpClient:
        """The client for ``id`` — same instance across restarts."""
        return self._require(id).client

    def status(self) -> list[ServerStatus]:
        """Snapshot of every registered server, registration order."""
        return [self._status_of(rec) for rec in self._servers.values()]

    def status_of(self, id: str) -> ServerStatus:
        """Snapshot of one server."""
        return self._status_of(self._require(id))

    def _require(self, id: str) -> _ManagedServer:
        rec = self._servers.get(id)
        if rec is None:
            raise McpConfigError(f"unknown server id: {id}")
        return rec

    def _status_of(self, rec: _ManagedServer) -> ServerStatus:
        return ServerStatus(
            id=rec.id,
            name=rec.name,
            state=rec.state,
            transport="stdio" if isinstance(rec.config, StdioConfig) else "http",
            restart_count=rec.restart_count,
            consecutive_failures=rec.consecutive_failures,
            last_error=rec.last_error,
            last_state_change=rec.last_state_change,
        )

    def _set_state(self, rec: _ManagedServer, state: ServerState) -> None:
        rec.state = state
        rec.last_state_change = datetime.now(timezone.utc)
        logger.info("MCP server state | id=%s state=%s", rec.id, state)
