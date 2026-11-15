"""App lifespan for the MCP fleet: build → register → start → publish.

Startup posture (spec decision 6): a server that can't start does NOT abort
app boot — its supervisor enters the backoff/crashed path with ERROR logs.
Deliberate divergence from db_lifespan's fail-fast: MCP servers are
peripheral; the schema is not. The same posture extends to config reads
(issue #21): a corrupt row is logged ERROR and skipped; a missing
db_session_factory warns and boots zero servers.

``configs=None`` (the default) loads enabled rows from the unified
database via ``app.state.db_session_factory`` — db_lifespan composes first.
An explicit iterable (including empty) bypasses the DB entirely.

``start_all``/``stop_all`` run in the lifespan task, satisfying the manager's
same-task requirement for manual task-group entry.
"""

import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from octave.mcp.config import ServerConfig
from octave.mcp.config_store import McpConfigStore, to_server_config
from octave.mcp.errors import McpConfigError
from octave.mcp.manager import ClientFactory, McpServerManager
from octave.mcp.registry import ToolRegistry

__all__ = ["mcp_lifespan"]

logger = logging.getLogger(__name__)


async def _entries_from_db(app: FastAPI) -> list[tuple[str, str, ServerConfig]]:
    """Enabled server rows, converted per row so corrupt rows skip individually.

    Peripheral posture: never raises. Missing factory → WARNING + empty.
    Bad row → ERROR (id + reason; env/header values never appear) + skip.
    """
    factory = getattr(app.state, "db_session_factory", None)
    if factory is None:
        logger.warning(
            "no db_session_factory on app.state: booting with zero MCP servers"
        )
        return []
    entries: list[tuple[str, str, ServerConfig]] = []
    async with factory() as session:
        for row in await McpConfigStore(session).list_rows(enabled_only=True):
            try:
                config = to_server_config(row)
            except McpConfigError as exc:
                logger.error("skipping corrupt mcp_servers row %s: %s", row.id, exc)
                continue
            entries.append((row.id, row.name, config))
    return entries


@asynccontextmanager
async def mcp_lifespan(
    app: FastAPI,
    *,
    configs: Iterable[tuple[str, str, ServerConfig]] | None = None,
    client_factory: ClientFactory | None = None,
) -> AsyncIterator[None]:
    """Manage the MCP fleet for the app's lifetime.

    ``configs`` yields ``(id, name, config)`` triples. ``None`` (the
    default) loads enabled servers from the database (#21); an explicit
    iterable — including empty — is used verbatim.
    """
    entries = list(configs) if configs is not None else await _entries_from_db(app)
    manager = McpServerManager(client_factory=client_factory)
    for server_id, name, config in entries:
        manager.register(id=server_id, name=name, config=config)
    await manager.start_all()
    app.state.mcp_manager = manager
    registry = ToolRegistry(manager=manager)
    await registry.start()
    app.state.mcp_registry = registry
    logger.info("MCP manager ready: %s server(s)", len(entries))
    try:
        yield
    finally:
        # Registry teardown first: listeners stop consuming streams before
        # connections unwind.
        await registry.stop()
        await manager.stop_all()
        logger.info("MCP manager stopped")
