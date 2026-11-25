"""Out-of-band session slide (issue #161).

The request path must stay write-free: an in-band touch upgrades the
deferred BEGIN to a write and deadlocks against WAL readers
(``SQLITE_BUSY_SNAPSHOT``, immune to busy_timeout). This helper opens its
own short-lived session, runs the single throttled UPDATE through
AuthStore, and commits — it IS the caller that owns the transaction, so
the #27 "stores never commit" convention holds.

Posture: best-effort. Awaited with a timeout (below the 5 s busy_timeout)
so a held write lock cancels the slide instead of delaying the request;
every failure is a warning, never an exception — the sliding window is
advisory-precision by design.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from octave.auth.config import AuthSettings
from octave.auth.store import AuthStore
from octave.auth.tokens import hash_token

__all__ = ["slide_session"]

logger = logging.getLogger(__name__)


async def slide_session(
    factory: async_sessionmaker[Any] | None,
    raw_token: str,
    *,
    settings: AuthSettings,
    now: datetime,
) -> bool:
    """Best-effort idle-expiry slide for one session token.

    Returns True when the row was actually slid (throttle passed). Any
    failure — missing factory, busy DB, timeout — returns False and logs;
    a dropped slide only shortens the cookie by <= the grace window.
    """
    if factory is None:
        return False

    async def _slide() -> bool:
        async with factory() as session:
            count = await AuthStore(session).touch_session_by_hash(
                hash_token(raw_token),
                now=now,
                idle_ttl_days=settings.idle_ttl_days,
                absolute_ttl_days=settings.absolute_ttl_days,
                grace_seconds=settings.touch_grace_seconds,
            )
            await session.commit()
            return count > 0

    try:
        return await asyncio.wait_for(_slide(), timeout=settings.touch_timeout_seconds)
    except Exception:
        logger.warning("session slide failed (advisory, ignored)", exc_info=True)
        return False
