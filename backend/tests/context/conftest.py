"""Vector-store-enabled fixtures for the context plane."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from octave.db.config import DbConfig
from octave.db.models import Base
from octave.db.sqlite_adapter import SqliteVecAdapter

DIM = 4


@pytest_asyncio.fixture
async def env(
    tmp_path: Path,
) -> AsyncIterator[tuple[SqliteVecAdapter, async_sessionmaker]]:
    config = DbConfig(
        adapter="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'context.db'}",
        embedding_dim=DIM,
    )
    adapter = SqliteVecAdapter(config)
    engine: AsyncEngine = adapter.make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await adapter.ensure_vector_store(conn)
    factory = adapter.make_session_factory(engine)
    yield adapter, factory
    await engine.dispose()
