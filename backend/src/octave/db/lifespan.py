"""Application lifespan: build the DB adapter, migrate, publish state.

Fail-fast posture (spec Decision 5): any startup failure propagates so the
process exits — serving traffic against an untrusted schema is worse than
a crash. The ``finally`` block releases adapter and engine on every path,
including the crash path, so no file handle leaks.

``upgrade``/``current`` are sync (Alembic drives a sync engine). That is
fine here: the lifespan runs before uvicorn accepts connections, so the
brief blocking costs nothing. If the loop ever matters, wrap in
``anyio.to_thread.run_sync`` — do not defer the wiring for it.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine

from octave.db.config import DatabaseSettings
from octave.db.errors import DbMigrationError
from octave.db.migrations import current, upgrade
from octave.db.registry import default_registry

__all__ = ["db_lifespan"]

logger = logging.getLogger(__name__)


@asynccontextmanager
async def db_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Wire the database for the app's lifetime.

    Startup: settings -> adapter -> migrations (gated by
    ``settings.auto_migrate``) -> engine -> vector store -> ``app.state``.
    Shutdown: ``adapter.aclose()`` + ``engine.dispose()``.
    """
    settings = DatabaseSettings()
    config = settings.to_db_config()
    adapter = default_registry.create(config)
    engine: AsyncEngine | None = None
    try:
        try:
            if settings.auto_migrate:
                upgrade(config.sync_url)
            elif current(config.sync_url) is None:
                raise DbMigrationError(
                    "database not migrated — run `alembic upgrade head` "
                    "or set OCTAVE_DB_AUTO_MIGRATE=true"
                )
            engine = adapter.make_engine()
            async with engine.begin() as connection:
                await adapter.ensure_vector_store(connection)
        except Exception:
            logger.exception("database startup failed; failing fast")
            raise
        app.state.db_adapter = adapter
        app.state.db_engine = engine
        app.state.db_session_factory = adapter.make_session_factory(engine)
        logger.info(
            "database ready: adapter=%s dim=%s",
            config.adapter,
            config.embedding_dim,
        )
        yield
    finally:
        await adapter.aclose()
        if engine is not None:
            await engine.dispose()
