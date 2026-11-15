"""McpConfigStore: CRUD + row conversion over mcp_servers (real SQLite).

Uses the shared engine/session_factory fixtures (tests/conftest.py):
temp-file SQLite through create_sqlite_engine, constraints live.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from octave.db.models import McpServer
from octave.mcp.config import HttpConfig, StdioConfig
from octave.mcp.config_store import McpConfigStore, to_server_config
from octave.mcp.errors import McpConfigError, McpServerNameTaken

# -- round-trips ---------------------------------------------------------------


async def test_stdio_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
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
            config=HttpConfig(
                url="http://h:9/t", headers={"Authorization": "Bearer x"}
            ),
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


# -- update / delete / set_enabled / list --------------------------------------


async def test_update_replaces_only_provided_fields(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        entry = await store.create(name="svc", config=StdioConfig(command="a"))
        updated = await store.update(entry.id, config=HttpConfig(url="http://h:1/m"))
        assert updated is not None
        assert isinstance(updated.config, HttpConfig)
        assert updated.name == "svc"  # untouched
        assert updated.enabled is True
        await session.commit()
    async with session_factory() as session:
        got = await McpConfigStore(session).get(entry.id)
        assert got is not None
        assert got.config == HttpConfig(url="http://h:1/m")


async def test_update_transport_swaps_back(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        entry = await store.create(
            name="flip", config=HttpConfig(url="http://h/m", headers={"A": "1"})
        )
        back = await store.update(
            entry.id, config=StdioConfig(command="x", args=["-q"])
        )
        assert back is not None
        assert isinstance(back.config, StdioConfig)
        assert back.config.args == ["-q"]
        await session.commit()
    async with session_factory() as session:
        row = await session.get(McpServer, entry.id)
        assert row is not None
        assert row.headers is None  # http columns cleared by the swap
        assert row.transport == "stdio"


async def test_update_name_collision_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        await store.create(name="alpha", config=StdioConfig(command="a"))
        beta = await store.create(name="beta", config=StdioConfig(command="b"))
        with pytest.raises(McpServerNameTaken):
            await store.update(beta.id, name="alpha")


async def test_update_absent_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await McpConfigStore(session).update("nope", enabled=False) is None


async def test_delete_returns_existence(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        entry = await store.create(name="gone", config=StdioConfig(command="a"))
        assert await store.delete(entry.id) is True
        assert await store.delete(entry.id) is False
        assert await store.get(entry.id) is None


async def test_set_enabled_toggles(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        entry = await store.create(name="toggle", config=StdioConfig(command="a"))
        assert await store.set_enabled(entry.id, False) is True
        assert await store.set_enabled("nope", True) is False
        await session.commit()
    async with session_factory() as session:
        got = await McpConfigStore(session).get(entry.id)
        assert got is not None and got.enabled is False


async def test_list_orders_by_created_at_then_id(
    session_factory: async_sessionmaker[AsyncSession], engine: AsyncEngine
) -> None:
    async with engine.begin() as conn:
        for sid, created in (
            ("z_old", "2026-01-01 00:00:00"),
            ("m_mid", "2026-01-01 00:00:00"),  # same instant: id tiebreak
            ("a_new", "2026-01-02 00:00:00"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO mcp_servers (id, name, transport, command, enabled, "
                    "created_at, updated_at) VALUES (:i, :n, 'stdio', 'cmd', 1, :c, :c)"
                ),
                {"i": sid, "n": sid, "c": created},
            )
    async with session_factory() as session:
        entries = await McpConfigStore(session).list_servers()
        assert [e.id for e in entries] == ["m_mid", "z_old", "a_new"]


async def test_list_enabled_only(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = McpConfigStore(session)
        await store.create(name="on", config=StdioConfig(command="a"))
        await store.create(name="off", config=StdioConfig(command="b"), enabled=False)
        await session.commit()
    async with session_factory() as session:
        enabled = await McpConfigStore(session).list_servers(enabled_only=True)
        assert [e.name for e in enabled] == ["on"]
        every = await McpConfigStore(session).list_servers()
        assert len(every) == 2


# -- transaction contract --------------------------------------------------------


async def test_store_never_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An uncommitted store write is invisible to a second session."""
    async with session_factory() as session:
        await McpConfigStore(session).create(
            name="quiet", config=StdioConfig(command="a")
        )
        # no commit
    async with session_factory() as session:
        assert await McpConfigStore(session).list_servers() == []
