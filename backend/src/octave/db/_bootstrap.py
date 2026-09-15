"""SQLite connection bootstrap — async_creator variant (Task 1 Spike A, Variant B).

The connect-event unwrap path (Variant A) failed the spike: under aiosqlite
the event fires on the wrong thread for ``enable_load_extension``. With an
``async_creator`` the extension is loaded inside the coroutine, on
aiosqlite's own thread, which is also the *correct* thread for
``load_extension`` — no cross-thread sqlite3 calls.

One of the two modules in this package allowed to touch ``sqlite_vec`` (the
other: ``sqlite_adapter``). Every new connection also gets FK enforcement on;
SQLite defaults leave it off per-connection.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite
import sqlite_vec
from sqlalchemy.engine import URL

__all__ = ["make_async_creator"]

logger = logging.getLogger(__name__)


def make_async_creator(url: URL) -> Callable[[], Awaitable[Any]]:
    """Return an ``async_creator`` callable for ``create_async_engine``."""
    db_path = url.database or ":memory:"

    async def _connect() -> aiosqlite.Connection:
        conn = await aiosqlite.connect(db_path)
        await conn.enable_load_extension(True)
        await conn.load_extension(sqlite_vec.loadable_path())
        await conn.enable_load_extension(False)
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA journal_mode=WAL")
        # Concurrent-writer collisions wait up to 5s for the write lock
        # instead of raising SQLITE_BUSY immediately (spec: Concurrency
        # posture). Policy beyond this (retry helpers) is deferred.
        await conn.execute("PRAGMA busy_timeout=5000")
        logger.debug("configured aiosqlite connection with vec0 and pragmas")
        return conn

    return _connect
