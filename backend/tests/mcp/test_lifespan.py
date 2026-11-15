"""mcp_lifespan + compose_lifespans: startup, shutdown, ordering."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from octave.app import compose_lifespans
from octave.db._bootstrap import create_sqlite_engine
from octave.db.models import Base
from octave.mcp.config import HttpConfig, StdioConfig
from octave.mcp.config_store import McpConfigStore
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


# -- DB-backed boot (configs=None default) --------------------------------------


def _db_lifespan(
    db_url: str, fake: FakeFactory, seed
):  # noqa: ANN202 - test helper
    """Lifespan that builds a migrated SQLite, seeds it, then boots mcp_lifespan."""

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_sqlite_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await seed(McpConfigStore(session))
            await session.commit()
        app.state.db_session_factory = factory
        async with mcp_lifespan(app, client_factory=fake):
            yield
        await engine.dispose()

    return _lifespan


async def _seed_enabled_and_disabled(store: McpConfigStore) -> None:
    await store.create(name="Alpha", config=StdioConfig(command="alpha"))
    await store.create(
        name="Ghost", config=StdioConfig(command="ghost"), enabled=False
    )


def test_db_boot_registers_enabled_rows_only(tmp_path) -> None:  # noqa: ANN001
    fake = FakeFactory()
    app = FastAPI(
        lifespan=_db_lifespan(f"sqlite:///{tmp_path / 'boot.db'}", fake, _seed_enabled_and_disabled)
    )
    with TestClient(app) as client:
        manager: McpServerManager = client.app.state.mcp_manager
        assert [s.name for s in manager.status()] == ["Alpha"]
        assert manager.status()[0].state == "connected"


async def _seed_with_corrupt_row(store: McpConfigStore) -> None:
    """One good row + one CHECK-violating row (PRAGMA bypasses the constraint
    so the defensive per-row skip path is exercised)."""
    await store.create(name="Good", config=HttpConfig(url="http://h:1/m"))
    await store._session.execute(  # noqa: SLF001 - test needs raw escape hatch
        text("PRAGMA ignore_check_constraints=ON")
    )
    await store._session.execute(  # noqa: SLF001
        text(
            "INSERT INTO mcp_servers (id, name, transport, command, url, enabled, "
            "created_at, updated_at) VALUES ('bad1', 'Broken', 'carrier-pigeon', "
            "NULL, NULL, 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        ),
    )
    await store._session.execute(  # noqa: SLF001
        text("PRAGMA ignore_check_constraints=OFF")
    )


def test_db_boot_skips_corrupt_row_and_boots_siblings(
    tmp_path, caplog
) -> None:  # noqa: ANN001
    import logging

    fake = FakeFactory()
    app = FastAPI(
        lifespan=_db_lifespan(
            f"sqlite:///{tmp_path / 'corrupt.db'}", fake, _seed_with_corrupt_row
        )
    )
    with caplog.at_level(logging.ERROR, logger="octave.mcp.lifespan"):
        with TestClient(app) as client:
            manager: McpServerManager = client.app.state.mcp_manager
            assert [s.name for s in manager.status()] == ["Good"]
    assert any("bad1" in rec.message or "Broken" in rec.message for rec in caplog.records)


def test_explicit_configs_bypass_db(tmp_path) -> None:  # noqa: ANN001
    """A passed configs iterable wins even when a session factory exists."""
    fake = FakeFactory()
    db_url = f"sqlite:///{tmp_path / 'bypass.db'}"

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_sqlite_engine(db_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await McpConfigStore(session).create(
                name="FromDb", config=StdioConfig(command="db-server")
            )
            await session.commit()
        app.state.db_session_factory = factory
        configs = (("s1", "Explicit", StdioConfig(command="fake")),)
        async with mcp_lifespan(app, configs=configs, client_factory=fake):
            yield
        await engine.dispose()

    app = FastAPI(lifespan=_lifespan)
    with TestClient(app) as client:
        manager: McpServerManager = client.app.state.mcp_manager
        assert [s.name for s in manager.status()] == ["Explicit"]
