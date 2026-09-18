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
from octave.mcp.errors import McpConfigError, McpError, McpNotConnectedError

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

    async def start_all(self) -> None:
        """Enter the task group and spawn one supervisor per server.

        Returns once supervisors are spawned — not once servers are healthy;
        observe readiness via ``status()``. Must be called from the same task
        as ``stop_all()`` (the task group is entered manually).
        """
        if self._task_group is not None:
            raise McpError("start_all() already called")
        self._task_group = anyio.create_task_group()
        await self._task_group.__aenter__()
        for rec in self._servers.values():
            if rec.state == "stopped":
                await self.start(rec.id)

    async def stop_all(self) -> None:
        """Cancel every supervisor and await teardown."""
        if self._task_group is None:
            return
        for rec in self._servers.values():
            if rec.scope is not None:
                rec.scope.cancel()
        task_group, self._task_group = self._task_group, None
        await task_group.__aexit__(None, None, None)

    async def start(self, id: str) -> None:
        """Spawn a stopped server's supervisor."""
        if self._task_group is None:
            raise McpError("manager not started — call start_all() first")
        rec = self._require(id)
        if rec.state != "stopped":
            raise McpError(f"server {id} already started (state={rec.state})")
        rec.scope = anyio.CancelScope()
        self._task_group.start_soon(self._supervise, rec)

    async def stop(self, id: str) -> None:
        """Cancel a supervisor; its ``finally`` block closes the client.

        Cancellation *is* the stop mechanism, so stop/restart cannot race.
        """
        rec = self._require(id)
        if rec.scope is not None:
            rec.scope.cancel()

    async def restart(self, id: str) -> None:
        """Supervisor-driven restart cycle; resets the failure counter.

        The only exit from ``crashed``. From ``stopped`` →
        ``McpNotConnectedError``.
        """
        rec = self._require(id)
        if rec.state == "stopped":
            raise McpNotConnectedError(f"cannot restart {id}: not started")
        rec.cmd_restart = True
        rec.wake.set()

    async def _supervise(self, rec: _ManagedServer) -> None:
        """Task body wrapper: the shielded teardown is the stop mechanism."""
        try:
            await self._run_server_loop(rec)
        finally:
            with anyio.CancelScope(shield=True):
                await rec.client.aclose()
            self._set_state(rec, "stopped")

    async def _run_server_loop(self, rec: _ManagedServer) -> None:
        """Supervision loop — the sole caller of rec.client lifecycle methods.

        Phases (spec state machine): initial connect → park on ``wake`` →
        restart cycles with exponential backoff → ``crashed`` parks until
        manual ``restart()``. Any unexpected exception is contained: logged,
        ``crashed``, parked — one server's bug never kills another's
        supervision and never loops silently.
        """
        settings = self._settings
        started = False
        with rec.scope:
            while True:
                try:
                    if not started:
                        started = True
                        self._set_state(rec, "starting")
                        if await self._attempt_connect(rec):
                            self._mark_connected(rec, count_restart=False)
                            continue  # healthy: park on wake, not the restart path
                        self._set_state(rec, "restarting")
                    elif rec.state in ("connected", "crashed"):
                        await rec.wake.wait()
                        rec.wake = anyio.Event()
                        reason, cmd = rec.reason, rec.cmd_restart
                        rec.reason, rec.cmd_restart = None, False
                        if rec.state == "connected":
                            if anyio.current_time() >= rec.stable_deadline:
                                rec.consecutive_failures = 0
                            if reason == "timeout" and not cmd:
                                if await self._probe_ok(rec):
                                    continue  # healthy-but-slow: keep serving
                        if cmd:
                            rec.consecutive_failures = 0
                        self._set_state(rec, "restarting")
                    await rec.client.aclose()
                    while rec.consecutive_failures < settings.restart_max_attempts:
                        rec.consecutive_failures += 1
                        delay = min(
                            settings.restart_base_delay_seconds
                            * 2 ** (rec.consecutive_failures - 1),
                            settings.restart_max_delay_seconds,
                        )
                        logger.warning(
                            "restarting MCP server | id=%s attempt=%s delay=%.2fs",
                            rec.id,
                            rec.consecutive_failures,
                            delay,
                        )
                        await anyio.sleep(delay)
                        if await self._attempt_connect(rec):
                            if cmd:
                                # Manual restart fully resets the failure
                                # streak (spec: restart(id) resets the counter).
                                rec.consecutive_failures = 0
                            self._mark_connected(rec, count_restart=True)
                            break
                    else:
                        logger.error(
                            "MCP server crashed | id=%s attempts=%s last_error=%s",
                            rec.id,
                            settings.restart_max_attempts,
                            rec.last_error,
                        )
                        self._set_state(rec, "crashed")
                except Exception:
                    logger.exception(
                        "MCP supervisor failure contained | id=%s", rec.id
                    )
                    self._set_state(rec, "crashed")

    async def _attempt_connect(self, rec: _ManagedServer) -> bool:
        """One connect try; McpError failures recorded, never raised to the loop."""
        try:
            await rec.client.connect(rec.config)
        except McpError as exc:
            rec.last_error = str(exc)
            logger.warning("MCP connect failed | id=%s error=%s", rec.id, exc)
            return False
        return True

    async def _probe_ok(self, rec: _ManagedServer) -> bool:
        """Confirming ping within ``probe_timeout_seconds`` (spec: probe
        budget). True only if the server answered in budget."""
        try:
            with anyio.fail_after(self._settings.probe_timeout_seconds):
                return await rec.client.ping()
        except (TimeoutError, McpError):
            return False

    def _mark_connected(self, rec: _ManagedServer, *, count_restart: bool) -> None:
        if count_restart:
            rec.restart_count += 1
        rec.stable_deadline = (
            anyio.current_time() + self._settings.stabilization_seconds
        )
        self._set_state(rec, "connected")
