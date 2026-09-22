"""MCP tool registry — fleet-wide tool inventory + execution surface.

Role (design spec #77): the tool-plane counterpart to the manager's
lifecycle-plane. Owns the per-server inventory cache, event-driven
invalidation (restart_count drift, notifications/tools/list_changed,
explicit refresh), and the (server_id, tool_name) call surface. Composes
on ``McpServerManager``'s public API only; never imports the ``mcp`` SDK.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anyio
from pydantic import BaseModel

from octave.mcp.errors import McpError
from octave.mcp.manager import McpServerManager, ServerState
from octave.mcp.types import ToolInfo, ToolResult

__all__ = ["ServerToolInventory", "ToolRegistry"]

logger = logging.getLogger(__name__)

TOOLS_LIST_CHANGED = "notifications/tools/list_changed"
"""MCP notification method signalling a server's tool-set changed."""


class ServerToolInventory(BaseModel):
    """One server's cached tool inventory + freshness metadata."""

    server_id: str
    server_name: str
    state: ServerState
    tools: list[ToolInfo]
    """Last-known-good discovery result; empty until the first success."""

    fetched_at: datetime | None = None
    """Start of the successful discovery that produced ``tools``."""

    last_error: str | None = None
    """Most recent discovery failure (Octave exception message, redacted)."""


@dataclass
class _CacheEntry:
    """Internal cache state for one server's inventory."""

    tools: list[ToolInfo] = field(default_factory=list)
    fetched_at: datetime | None = None
    last_error: str | None = None
    restart_count_at_fetch: int = -1
    lock: anyio.Lock = field(default_factory=anyio.Lock)


class ToolRegistry:
    """Fleet-wide tool inventory + execution surface over a manager.

    Safe for concurrent use: cache mutations happen under per-server locks;
    reads snapshot plain attributes (the manager's lock-free-read posture).
    """

    def __init__(self, *, manager: McpServerManager) -> None:
        self._manager = manager
        self._entries: dict[str, _CacheEntry] = {}

    # ---- discovery --------------------------------------------------

    async def refresh(self, id: str) -> None:
        """Re-run tools/list for one server; never raises on I/O failure.

        Unknown id -> ``McpConfigError`` (the only raise). Discovery failure
        keeps last-known-good tools and records ``last_error`` (spec
        decision 4: read paths must stay safe).
        """
        self._manager.status_of(id)  # unknown id -> McpConfigError
        entry = self._entry(id)
        async with entry.lock:
            await self._refresh_locked(id)

    async def _refresh_locked(self, id: str) -> None:
        """One discovery round; caller must hold the server lock."""
        entry = self._entry(id)
        try:
            tools = await self._manager.get_client(id).list_tools()
        except McpError as exc:
            # Last-known-good posture: keep stale tools, record the
            # failure, leave fetched_at untouched so the next read retries.
            entry.last_error = str(exc)
            logger.exception("MCP tool discovery failed | id=%s error=%s", id, exc)
            return
        entry.tools = tools
        entry.fetched_at = datetime.now(UTC)
        entry.restart_count_at_fetch = self._manager.status_of(id).restart_count
        entry.last_error = None
        logger.info("MCP tool inventory refreshed | id=%s tools=%s", id, len(tools))

    # ---- reads ------------------------------------------------------

    async def inventory(self) -> list[ServerToolInventory]:
        """Snapshot of every registered server, registration order.

        Stale/unpopulated entries are refreshed synchronously first.
        """
        return [await self._read(s.id) for s in self._manager.status()]

    async def tools_for(self, id: str) -> list[ToolInfo]:
        """One server's tools; refreshed synchronously when stale."""
        inventory = await self._read(id)
        return list(inventory.tools)

    async def _read(self, id: str) -> ServerToolInventory:
        entry = self._entry(id)
        if self._is_stale(id, entry):
            async with entry.lock:
                if self._is_stale(id, entry):  # recheck: concurrent reads coalesce
                    await self._refresh_locked(id)
        status = self._manager.status_of(id)  # unknown id -> McpConfigError
        return ServerToolInventory(
            server_id=id,
            server_name=status.name,
            state=status.state,
            tools=list(entry.tools),
            fetched_at=entry.fetched_at,
            last_error=entry.last_error,
        )

    def _is_stale(self, id: str, entry: _CacheEntry) -> bool:
        """Never fetched, or the server restarted since the fetch.

        restart_count drift detects connect/restart/crash-recovery without
        a manager hook — the manager stays "policy only" (#18 spec).
        """
        if entry.fetched_at is None:
            return True
        return (
            self._manager.status_of(id).restart_count
            != entry.restart_count_at_fetch
        )

    # ---- execution --------------------------------------------------

    async def call_tool(
        self, id: str, name: str, arguments: dict[str, Any] | None = None
    ) -> ToolResult:
        """Invoke a tool on one server; errors propagate untranslated.

        No cache pre-validation: the server is the source of truth, and a
        stale inventory must never block a valid call. Timeout, JSON-RPC,
        and connection semantics are the client's (``McpClient.call_tool``).
        """
        client = self._manager.get_client(id)  # unknown id -> McpConfigError
        return await client.call_tool(name, arguments=arguments)

    def _entry(self, id: str) -> _CacheEntry:
        return self._entries.setdefault(id, _CacheEntry())
