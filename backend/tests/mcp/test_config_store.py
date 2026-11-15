"""McpConfigStore: CRUD + row conversion over mcp_servers (real SQLite).

Uses the shared engine/session_factory fixtures (tests/conftest.py):
temp-file SQLite through create_sqlite_engine, constraints live.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, AsyncSession

from octave.db.models import McpServer
from octave.mcp.config import HttpConfig, StdioConfig
from octave.mcp.config_store import McpConfigStore, McpServerEntry, to_server_config
from octave.mcp.errors import McpConfigError, McpServerNameTaken

# -- round-trips ---------------------------------------------------------------


async def test_stdio_round_trip(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        entry = await store.create(
            name="files",
            config=StdioConfig(
                command="npx fs-server", args=["--root", "/data"], env={"KEY": "v"}
            ),
        )
        await session.commit()
        assert entry.name == "files"
        assert entry.enabled is True
        assert isinstance(entry.config, StdioConfig)
        assert entry.config.command == "npx fs-server"
        assert entry.config.args == ["--root", "/data"]
        assert entry.config.env == {"KEY": "v"}

    async with session_factory() as session:
        got = await McpConfigStore(session).get(entry.id)
        assert got is not None
        assert got.config == entry.config


async def test_http_round_trip_with_headers(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        entry = await store.create(
            name="remote",
            config=HttpConfig(url="http://h:9/t", headers={"Authorization": "Bearer x"}),
        )
        await session.commit()
    async with session_factory() as session:
        got = await McpConfigStore(session).get(entry.id)
        assert got is not None
        assert isinstance(got.config, HttpConfig)
        assert got.config.url == "http://h:9/t"
        assert got.config.headers == {"Authorization": "Bearer x"}


async def test_null_json_columns_become_dataclass_defaults(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        entry = await McpConfigStore(session).create(
            name="bare", config=StdioConfig(command="cmd")
        )
        await session.commit()
    async with session_factory() as session:
        got = await McpConfigStore(session).get(entry.id)
        assert got is not None
        assert got.config.args == []  # type: ignore[attr-defined]
        assert got.config.env == {}  # type: ignore[attr-defined]


# -- name collision ------------------------------------------------------------


async def test_create_duplicate_name_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        await store.create(name="dup", config=StdioConfig(command="a"))
        with pytest.raises(McpServerNameTaken):
            await store.create(name="dup", config=StdioConfig(command="b"))


# -- get / conversion errors -----------------------------------------------------


def test_to_server_config_rejects_incoherent_rows() -> None:
    # In-memory rows (never flushed): the CHECK constraint makes these
    # unreachable via SQL, so the converter is tested directly.
    with pytest.raises(McpConfigError):
        to_server_config(McpServer(id="x", name="x", transport="carrier-pigeon"))
    with pytest.raises(McpConfigError):
        to_server_config(McpServer(id="x", name="x", transport="stdio", command=None))
    with pytest.raises(McpConfigError):
        to_server_config(McpServer(id="x", name="x", transport="http", url=None))


async def test_get_absent_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await McpConfigStore(session).get("nope") is None
