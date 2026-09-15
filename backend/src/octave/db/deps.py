"""FastAPI dependency resolver for database sessions.

Thin seam by design (spec Decision 5): the lifespan work item (follow-up
issue 1) will populate ``app.state.db_session_factory`` from settings and run
migrations. This module only proves the seam resolves, mirroring
``octave.mcp.deps``.
"""

from collections.abc import AsyncIterator

from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

__all__ = ["get_db_session"]


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield a session from ``app.state.db_session_factory``.

    Rolls back on any exception so a failed request cannot leave a partial
    transaction. Does not commit — callers own transaction boundaries.
    """
    factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "db_session_factory", None
    )
    if factory is None:
        raise HTTPException(
            status_code=503, detail="database session factory not configured"
        )
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
