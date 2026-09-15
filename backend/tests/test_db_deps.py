"""get_db_session resolver: app.state lookup, overrides, 503 when unset."""

from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.db.deps import get_db_session
from octave.db.models import Base, User


def _probe_app() -> FastAPI:
    """A minimal app whose only route depends on a DB session."""
    app = FastAPI()

    @app.get("/probe")
    async def _probe(
        session: AsyncSession = Depends(get_db_session),
    ) -> dict[str, int]:
        return {"users": 0}

    @app.get("/probe-write")
    async def _probe_write(
        session: AsyncSession = Depends(get_db_session),
    ) -> dict[str, str]:
        session.add(User(id="u_probe", display_name="Probe"))
        await session.commit()
        return {"id": "u_probe"}

    return app


@pytest.fixture
def factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    """Schema built with a SYNC engine; the async engine is only ever used
    inside TestClient's loop. Building it with ``asyncio.run`` here would
    strand pooled connections on a closed event loop."""
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import create_async_engine

    path = tmp_path / "deps.db"
    sync_engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    return async_sessionmaker(
        create_async_engine(f"sqlite+aiosqlite:///{path}"), expire_on_commit=False
    )


def test_unset_state_returns_503() -> None:
    response = TestClient(_probe_app()).get("/probe")
    assert response.status_code == 503
    assert response.json()["detail"] == "database session factory not configured"


def test_state_provides_session(factory: async_sessionmaker[AsyncSession]) -> None:
    app = _probe_app()
    app.state.db_session_factory = factory
    response = TestClient(app).get("/probe")
    assert response.status_code == 200


def test_session_writes_through(factory: async_sessionmaker[AsyncSession]) -> None:
    app = _probe_app()
    app.state.db_session_factory = factory
    response = TestClient(app).get("/probe-write")
    assert response.status_code == 200


def test_dependency_override_wins() -> None:
    app = _probe_app()
    app.dependency_overrides[get_db_session] = lambda: None
    response = TestClient(app).get("/probe")
    assert response.status_code == 200
