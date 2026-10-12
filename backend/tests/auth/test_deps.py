"""Auth deps: cookie -> identity; require_owner gates by role."""

from pathlib import Path

import pytest
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.auth.config import AuthSettings
from octave.auth.deps import SESSION_COOKIE, get_current_user, require_owner
from octave.auth.passwords import DummyHasher
from octave.auth.store import AuthStore
from octave.db._bootstrap import create_sqlite_engine
from octave.db.deps import get_db_session
from octave.db.models import Base, User
from octave.db.types import UserRole


@pytest.fixture
async def factory(tmp_path: Path) -> async_sessionmaker[AsyncSession]:
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'deps.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def probe_app(factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = FastAPI()
    app.state.db_session_factory = factory
    app.state.auth_hasher = DummyHasher()
    app.state.auth_settings = AuthSettings()

    @app.get("/whoami")
    async def whoami(user: User = Depends(get_current_user)) -> dict:
        return {"username": user.username}

    @app.get("/owner-only")
    async def owner_only(user: User = Depends(require_owner)) -> dict:
        return {"username": user.username}

    @app.post("/seed")
    async def seed(
        session: AsyncSession = Depends(get_db_session), username: str = "alice"
    ) -> dict:
        store = AuthStore(session)
        user = await store.create_user(
            username=username,
            display_name=username.title(),
            password_hash="dummy$pw",
        )
        await session.commit()
        return {"id": user.id}

    return app


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_anonymous_gets_401(probe_app: FastAPI) -> None:
    async with await _client(probe_app) as client:
        response = await client.get("/whoami")
    assert response.status_code == 401


async def test_valid_cookie_resolves_identity(
    probe_app: FastAPI, factory: async_sessionmaker[AsyncSession]
) -> None:
    from octave.auth.service import AuthService

    async with factory() as session:
        await AuthStore(session).create_user(
            username="alice", display_name="Alice", password_hash="dummy$pw"
        )
        await session.commit()
        service = AuthService(
            store=AuthStore(session), hasher=DummyHasher(), settings=AuthSettings()
        )
        token = await service.login("alice", "pw")
        await session.commit()
    async with await _client(probe_app) as client:
        client.cookies.set(SESSION_COOKIE, token)
        response = await client.get("/whoami")
    assert response.status_code == 200
    assert response.json() == {"username": "alice"}


async def test_garbage_cookie_gets_401(probe_app: FastAPI) -> None:
    async with await _client(probe_app) as client:
        client.cookies.set(SESSION_COOKIE, "not-a-real-token")
        response = await client.get("/whoami")
    assert response.status_code == 401


async def test_require_owner_allows_owner_rejects_member(
    probe_app: FastAPI, factory: async_sessionmaker[AsyncSession]
) -> None:
    from octave.auth.service import AuthService

    async with factory() as session:
        store = AuthStore(session)
        owner = await store.create_user(
            username="alice", display_name="Alice", password_hash="dummy$pw"
        )
        await store.set_role(owner, UserRole.OWNER)
        await store.create_user(
            username="bob", display_name="Bob", password_hash="dummy$pw"
        )
        await session.commit()
        service = AuthService(
            store=AuthStore(session), hasher=DummyHasher(), settings=AuthSettings()
        )
        owner_token = await service.login("alice", "pw")
        member_token = await service.login("bob", "pw")
        await session.commit()
    async with await _client(probe_app) as client:
        client.cookies.set(SESSION_COOKIE, member_token)
        assert (await client.get("/owner-only")).status_code == 403
        client.cookies.set(SESSION_COOKIE, owner_token)
        assert (await client.get("/owner-only")).status_code == 200


async def test_httpexception_shape_for_anonymous(probe_app: FastAPI) -> None:
    async with await _client(probe_app) as client:
        response = await client.get("/whoami")
    body = response.json()
    assert isinstance(body.get("detail"), str)
    assert response.status_code == HTTPException(401).status_code
