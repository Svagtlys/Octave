"""FastAPI dependency resolver for the MCP server manager.

Thin seam (spec decision 4): ``mcp_lifespan`` publishes
``app.state.mcp_manager``; routes resolve through it and pick servers with
``manager.get_client(id)``. ``get_mcp_client`` was removed in #18 — with N
servers it cannot resolve "the" client.
"""

from fastapi import HTTPException, Request

from octave.mcp.manager import McpServerManager

__all__ = ["get_mcp_manager"]


async def get_mcp_manager(request: Request) -> McpServerManager:
    """Resolve the app-wide manager from ``app.state.mcp_manager``.

    Raises 503 while no manager is configured — Octave boots fine without
    any MCP servers.
    """
    manager: McpServerManager | None = getattr(
        request.app.state, "mcp_manager", None
    )
    if manager is None:
        raise HTTPException(status_code=503, detail="MCP manager not configured")
    return manager
