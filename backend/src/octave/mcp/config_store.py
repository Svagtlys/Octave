"""MCP server config persistence — CRUD over the ``mcp_servers`` table.

Plane-owned store (the AuthStore pattern): one ``AsyncSession`` per instance,
**never commits** — callers own transaction boundaries. The row→ServerConfig
conversion lives here (mcp-plane knowledge); ``octave.db`` never imports this
plane, only the reverse.

``env``/``headers`` may carry secrets: never log values. The redacting
``__repr__`` on StdioConfig/HttpConfig already protects log lines; entries
embed those configs.

Two read surfaces: strict entry reads (``get``/``list_servers`` — loud
``McpConfigError`` on rows that bypassed write-side validation) and raw rows
(``list_rows`` — same-plane tolerant readers, e.g. the lifespan's
skip-corrupt-row boot posture).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from octave.db.models import McpServer
from octave.mcp.config import HttpConfig, ServerConfig, StdioConfig
from octave.mcp.errors import McpConfigError, McpServerNameTaken

__all__ = ["McpConfigStore", "McpServerEntry", "to_server_config"]


@dataclass(frozen=True)
class McpServerEntry:
    """One persisted server, ready to register with the manager."""

    id: str
    name: str
    enabled: bool
    config: ServerConfig


def to_server_config(row: McpServer) -> ServerConfig:
    """Reconstruct the domain config from the transport columns.

    Raises ``McpConfigError`` on rows that bypassed write-side validation
    (unknown transport, stdio without command, http without url) — loud
    beats wrong: a bad connection attempt tells you more than a silent
    repair. Under the shipped CHECK constraint these rows are unreachable
    via SQL; this guards future engines without the constraint.
    """
    if row.transport == "stdio":
        if row.command is None:
            raise McpConfigError(f"mcp server {row.id!r}: stdio row without command")
        return StdioConfig(
            command=row.command, args=list(row.args or []), env=dict(row.env or {})
        )
    if row.transport == "http":
        if row.url is None:
            raise McpConfigError(f"mcp server {row.id!r}: http row without url")
        return HttpConfig(url=row.url, headers=dict(row.headers or {}))
    raise McpConfigError(
        f"mcp server {row.id!r}: unknown transport {row.transport!r}"
    )


def _columns_for(config: ServerConfig) -> dict[str, Any]:
    """Derive the transport columns from the domain config.

    The isinstance branch mirrors the DB CHECK constraint: shape coherence
    is enforced by the type at write time.
    """
    if isinstance(config, StdioConfig):
        return {
            "transport": "stdio",
            "command": config.command,
            "args": list(config.args),
            "env": dict(config.env),
            "url": None,
            "headers": None,
        }
    if isinstance(config, HttpConfig):
        return {
            "transport": "http",
            "command": None,
            "args": None,
            "env": None,
            "url": config.url,
            "headers": dict(config.headers),
        }
    raise McpConfigError(f"unsupported config type: {type(config).__name__}")


def _to_entry(row: McpServer) -> McpServerEntry:
    return McpServerEntry(
        id=row.id, name=row.name, enabled=row.enabled, config=to_server_config(row)
    )


class McpConfigStore:
    """CRUD over ``mcp_servers``. One session per instance; never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, name: str, config: ServerConfig, enabled: bool = True
    ) -> McpServerEntry:
        """Insert one server. Raises ``McpServerNameTaken`` on collision."""
        if await self._get_by_name(name) is not None:
            raise McpServerNameTaken(name)
        row = McpServer(
            id=uuid.uuid4().hex, name=name, enabled=enabled, **_columns_for(config)
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:  # race between check and insert
            raise McpServerNameTaken(name) from exc
        return _to_entry(row)

    async def get(self, server_id: str) -> McpServerEntry | None:
        row = await self._session.get(McpServer, server_id)
        return None if row is None else _to_entry(row)

    async def list_rows(self, *, enabled_only: bool = False) -> Sequence[McpServer]:
        """Raw ORM rows, oldest first — for same-plane tolerant readers
        (the lifespan converts per row and skips corrupt ones)."""
        stmt = select(McpServer).order_by(McpServer.created_at, McpServer.id)
        if enabled_only:
            stmt = stmt.where(McpServer.enabled.is_(True))
        return (await self._session.execute(stmt)).scalars().all()

    async def _get_by_name(self, name: str) -> McpServer | None:
        result = await self._session.execute(
            select(McpServer).where(McpServer.name == name)
        )
        return result.scalars().first()
