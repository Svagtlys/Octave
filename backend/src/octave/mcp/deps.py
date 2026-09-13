"""FastAPI dependency resolver for the MCP client.

Thin seam by design (spec decision 5): the lifecycle manager (roadmap #4)
will populate ``app.state.mcp_client`` via a lifespan once server configs
exist. This module only proves the seam resolves.
"""

from fastapi import HTTPException, Request

from octave.mcp.client import McpClient

__all__ = ["get_mcp_client"]


async def get_mcp_client(request: Request) -> McpClient:
    """Resolve the app-wide MCP client from ``app.state.mcp_client``.

    Raises 503 while no client is configured — Octave boots fine without
    any MCP servers.
    """
    client: McpClient | None = getattr(request.app.state, "mcp_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="MCP client not configured")
    return client
