"""get_mcp_manager resolver: app.state lookup, overrides, 503 when unset.

get_mcp_client was removed in #18: with N managed servers it cannot resolve
"the" client — consumers pick a server via manager.get_client(id).
"""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from octave.mcp.deps import get_mcp_manager, get_tool_registry
from octave.mcp.manager import McpServerManager
from octave.mcp.registry import ToolRegistry


def _probe_app() -> FastAPI:
    """A minimal app whose only route depends on the manager."""
    app = FastAPI()

    @app.get("/probe")
    async def _probe(
        manager: McpServerManager = Depends(get_mcp_manager),
    ) -> dict[str, str]:
        return {"manager": type(manager).__name__}

    return app


def test_unset_state_returns_503() -> None:
    response = TestClient(_probe_app()).get("/probe")
    assert response.status_code == 503
    assert response.json()["detail"] == "MCP manager not configured"


def test_state_provides_manager() -> None:
    app = _probe_app()
    app.state.mcp_manager = McpServerManager()
    response = TestClient(app).get("/probe")
    assert response.status_code == 200
    assert response.json() == {"manager": "McpServerManager"}


def test_dependency_override_wins() -> None:
    class FakeManager(McpServerManager):
        """No-op stand-in proving routes resolve through the override seam."""

    app = _probe_app()
    app.dependency_overrides[get_mcp_manager] = lambda: FakeManager()
    response = TestClient(app).get("/probe")
    assert response.status_code == 200
    assert response.json() == {"manager": "FakeManager"}


def test_get_mcp_client_is_gone() -> None:
    import octave.mcp
    import octave.mcp.deps

    assert not hasattr(octave.mcp, "get_mcp_client")
    assert not hasattr(octave.mcp.deps, "get_mcp_client")


def _registry_probe_app() -> FastAPI:
    """A minimal app whose only route depends on the registry."""
    app = FastAPI()

    @app.get("/probe")
    async def _probe(
        registry: ToolRegistry = Depends(get_tool_registry),
    ) -> dict[str, str]:
        return {"registry": type(registry).__name__}

    return app


def test_registry_unset_returns_503() -> None:
    response = TestClient(_registry_probe_app()).get("/probe")
    assert response.status_code == 503
    assert response.json()["detail"] == "MCP tool registry not configured"


def test_registry_state_provides() -> None:
    app = _registry_probe_app()
    app.state.mcp_registry = ToolRegistry(manager=McpServerManager())
    response = TestClient(app).get("/probe")
    assert response.status_code == 200
    assert response.json() == {"registry": "ToolRegistry"}


def test_registry_dependency_override_wins() -> None:
    class FakeRegistry(ToolRegistry):
        """No-op stand-in proving routes resolve through the override seam."""

    app = _registry_probe_app()
    app.dependency_overrides[get_tool_registry] = lambda: FakeRegistry(
        manager=McpServerManager()
    )
    response = TestClient(app).get("/probe")
    assert response.status_code == 200
    assert response.json() == {"registry": "FakeRegistry"}
