"""Shared fixtures: temp-file SQLite DB, async engine, session factory.

Temp FILE (not :memory:) so every connection shares one database, and
constraints are exercised on a real SQLite file — no mocks.

The connect hook uses the Variant B ``async_creator`` (see Task 1 Spike A):
aiosqlite's raw sqlite3 connection lives on aiosqlite's own thread, so
PRAGMAs executed from the connect-event thread raise a threading
ProgrammingError. Inside the async_creator coroutine everything runs on
aiosqlite's thread, where PRAGMAs apply correctly — a cursor handed back
by the async adapter object would silently never execute.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from octave.db._bootstrap import create_sqlite_engine
from octave.db.models import Base


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """Fresh SQLite file per test (tests stay independent per coding rules).

    Built through ``create_sqlite_engine``, the same choke point production
    uses (``SqliteVecAdapter.make_engine``): PRAGMAs + explicit-BEGIN
    transaction control so savepoints do not act as commits.
    """
    eng = create_sqlite_engine(f"sqlite:///{tmp_path / 'test.db'}")

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
