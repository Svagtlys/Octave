"""get_mcp_client resolver: app.state lookup, overrides, 503 when unset."""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from octave.mcp.client import McpClient
from octave.mcp.deps import get_mcp_client


def _probe_app() -> FastAPI:
    """A minimal app whose only route depends on the MCP client."""
    app = FastAPI()

    @app.get("/probe")
    async def _probe(client: McpClient = Depends(get_mcp_client)) -> dict[str, str]:
        return {"client": type(client).__name__}

    return app


def test_unset_state_returns_503() -> None:
    response = TestClient(_probe_app()).get("/probe")
    assert response.status_code == 503
    assert response.json()["detail"] == "MCP client not configured"


def test_state_provides_client() -> None:
    app = _probe_app()
    app.state.mcp_client = McpClient()  # never connected — resolver only wires DI
    response = TestClient(app).get("/probe")
    assert response.status_code == 200
    assert response.json() == {"client": "McpClient"}


def test_dependency_override_wins() -> None:
    class FakeMcpClient(McpClient):
        """No-op stand-in proving routes resolve through the override seam."""

    app = _probe_app()
    app.dependency_overrides[get_mcp_client] = lambda: FakeMcpClient()
    response = TestClient(app).get("/probe")
    assert response.status_code == 200
    assert response.json() == {"client": "FakeMcpClient"}
