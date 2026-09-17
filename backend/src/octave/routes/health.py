"""Aggregate health endpoint: per-component checks with an overall verdict.

Status codes carry the signal for monitors (Uptime Kuma, Gatus, compose
HEALTHCHECK, k8s probes); the body serves optional JSON assertions.
Vocabulary is ours (ok/degraded/unavailable) — no monitor requires the
Actuator convention. Adding a component = one check function + one CHECKS
entry. The check-fn contract is where future "optional component must not
degrade the whole app" policy lands (spec Decision 4, deferred).
"""

import logging
import os
from collections.abc import Awaitable, Callable
from importlib import metadata

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)
router = APIRouter()

CheckFn = Callable[[Request], Awaitable[dict[str, str]]]


async def _check_db(request: Request) -> dict[str, str]:
    """SELECT 1 through the shared session factory. Never raises."""
    factory: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "db_session_factory", None
    )
    if factory is None:
        return {"status": "unavailable"}
    try:
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        logger.warning("database health ping failed", exc_info=True)
        return {"status": "unavailable"}
    return {"status": "ok"}


CHECKS: dict[str, CheckFn] = {"db": _check_db}


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    results = {name: await check(request) for name, check in CHECKS.items()}
    healthy = all(result["status"] == "ok" for result in results.values())
    body: dict[str, object] = {"status": "ok" if healthy else "degraded", **results}
    return JSONResponse(status_code=200 if healthy else 503, content=body)


@router.get("/version")
async def version() -> dict[str, str]:
    try:
        v = metadata.version("octave-backend")
    except metadata.PackageNotFoundError:
        v = "unknown"
    env = os.getenv("OCTAVE_ENV", "development")
    return {"version": v, "env": env}
