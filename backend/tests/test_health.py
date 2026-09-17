"""Aggregate health: per-component checks, overall verdict, status codes."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from octave.app import app as global_app
from octave.routes.health import router


def _probe_app() -> FastAPI:
    """Fresh app with only the health router — no lifespan, no shared state."""
    probe = FastAPI()
    probe.include_router(router, prefix="/api")
    return probe


async def _get(app: FastAPI) -> tuple[int, dict]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")
    return response.status_code, response.json()


@pytest.fixture
def working_factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Empty but valid SQLite file — SELECT 1 works on any file."""
    engine = create_engine(f"sqlite:///{tmp_path / 'health.db'}")
    engine.dispose()
    return async_sessionmaker(
        create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'health.db'}"),
        expire_on_commit=False,
    )


@pytest.mark.asyncio
async def test_health_ok_when_db_up(
    working_factory: async_sessionmaker[AsyncSession],
) -> None:
    app = _probe_app()
    app.state.db_session_factory = working_factory
    status, body = await _get(app)
    assert status == 200
    assert body == {"status": "ok", "db": {"status": "ok"}}


@pytest.mark.asyncio
async def test_health_503_when_factory_unset() -> None:
    status, body = await _get(_probe_app())
    assert status == 503
    assert body == {"status": "degraded", "db": {"status": "unavailable"}}


@pytest.mark.asyncio
async def test_health_503_when_ping_fails() -> None:
    factory = async_sessionmaker(
        create_async_engine("sqlite+aiosqlite:////nonexistent-dir-42/x.db"),
        expire_on_commit=False,
    )
    app = _probe_app()
    app.state.db_session_factory = factory
    status, body = await _get(app)
    assert status == 503
    assert body == {"status": "degraded", "db": {"status": "unavailable"}}


@pytest.mark.asyncio
async def test_global_app_reports_db_down_without_lifespan() -> None:
    """Contract: ASGITransport never runs the lifespan, so the global app
    has no session factory — health must honestly report degraded."""
    status, body = await _get(global_app)
    assert status == 503
    assert body["status"] == "degraded"
