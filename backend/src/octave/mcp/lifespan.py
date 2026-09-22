"""App lifespan for the MCP fleet: build → register → start → publish.

Startup posture (spec decision 6): a server that can't start does NOT abort
app boot — its supervisor enters the backoff/crashed path with ERROR logs.
Deliberate divergence from db_lifespan's fail-fast: MCP servers are
peripheral; the schema is not.

``start_all``/``stop_all`` run in the lifespan task, satisfying the manager's
same-task requirement for manual task-group entry.
"""

import logging
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from octave.mcp.config import ServerConfig
from octave.mcp.manager import ClientFactory, McpServerManager
from octave.mcp.registry import ToolRegistry

__all__ = ["mcp_lifespan"]

logger = logging.getLogger(__name__)


@asynccontextmanager
async def mcp_lifespan(
    app: FastAPI,
    *,
    configs: Iterable[tuple[str, str, ServerConfig]] = (),
    client_factory: ClientFactory | None = None,
) -> AsyncIterator[None]:
    """Manage the MCP fleet for the app's lifetime.

    ``configs`` yields ``(id, name, config)`` triples. The default empty
    source keeps Octave booting with zero MCP servers until config
    persistence (#7) replaces it with DB rows.
    """
    entries = list(configs)
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
