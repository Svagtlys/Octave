"""mcp_lifespan + compose_lifespans: startup, shutdown, ordering."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from octave.app import compose_lifespans
from octave.mcp.config import StdioConfig
from octave.mcp.lifespan import mcp_lifespan
from octave.mcp.manager import McpServerManager
from octave.mcp.registry import ToolRegistry
from tests.mcp.test_manager import FakeFactory


def _app_with(factory: FakeFactory) -> FastAPI:
    configs = (("s1", "Server One", StdioConfig(command="fake")),)

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with mcp_lifespan(app, configs=configs, client_factory=factory):
            yield

    return FastAPI(lifespan=_lifespan)


def test_startup_connects_and_shutdown_closes() -> None:
    factory = FakeFactory()
    app = _app_with(factory)
    with TestClient(app) as client:
        manager: McpServerManager = client.app.state.mcp_manager
        status = manager.status_of("s1")
        assert status.state == "connected"
        assert status.name == "Server One"
    assert factory.clients[0].aclose_calls >= 1
    assert manager.status_of("s1").state == "stopped"


def test_default_configs_boots_empty() -> None:
    app = FastAPI(lifespan=mcp_lifespan)
    with TestClient(app) as client:
        assert client.app.state.mcp_manager.status() == []


def test_compose_lifespans_orders_start_and_stop() -> None:
    events: list[str] = []

    def make_lifespan(tag: str):  # noqa: ANN202 - test helper
        @asynccontextmanager
        async def _lf(_app: FastAPI) -> AsyncIterator[None]:
            events.append(f"start-{tag}")
            yield
            events.append(f"stop-{tag}")

        return _lf

    app = FastAPI(
        lifespan=compose_lifespans(make_lifespan("a"), make_lifespan("b"))
    )
    with TestClient(app):
        assert events == ["start-a", "start-b"]
    assert events == ["start-a", "start-b", "stop-b", "stop-a"]


def test_startup_publishes_tool_registry() -> None:
    factory = FakeFactory()
    app = _app_with(factory)
    with TestClient(app) as client:
        assert isinstance(client.app.state.mcp_registry, ToolRegistry)


def test_registry_stops_cleanly_on_shutdown() -> None:
    """Shutdown must not hang: registry listeners cancelled before aclose."""
    factory = FakeFactory()
    app = _app_with(factory)
    with TestClient(app):
        pass
    assert factory.clients[0].aclose_calls >= 1


def test_default_configs_publish_empty_registry() -> None:
    app = FastAPI(lifespan=mcp_lifespan)
    with TestClient(app) as client:
        registry = client.app.state.mcp_registry
        assert isinstance(registry, ToolRegistry)
