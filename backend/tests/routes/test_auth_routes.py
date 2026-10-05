"""Auth routes over the real app surface: cookie flow, admin gates, guard."""

from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.app import app as global_app
from octave.auth.deps import SESSION_COOKIE, get_current_user
from octave.auth.passwords import DummyHasher
from octave.db._bootstrap import create_sqlite_engine
from octave.db.models import Base, User
from octave.db.models.auth import AuthSession
from octave.db.models.base import utcnow
from octave.middleware import add_error_handlers
from octave.routes.auth import router as auth_router


async def _auth_app(
    tmp_path: Path, *, cookie_secure: bool = False
) -> tuple[FastAPI, async_sessionmaker[AsyncSession]]:
    from octave.auth.config import AuthSettings

    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(f"sqlite:///{tmp_path / 'routes.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.state.db_session_factory = factory
    app.state.auth_hasher = DummyHasher()
    app.state.auth_settings = AuthSettings(cookie_secure=cookie_secure)
    add_error_handlers(app)
    app.include_router(auth_router, prefix="/api")
    return app, factory


@pytest.fixture
async def app_and_factory(tmp_path: Path):
    return await _auth_app(tmp_path)


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _bootstrap(client: AsyncClient) -> None:
    response = await client.post(
        "/api/auth/bootstrap",
        json={"username": "alice", "password": "pw", "display_name": "Alice"},
    )
    assert response.status_code == 201


# -- status / bootstrap ------------------------------------------------------


async def test_status_reflects_setup_state(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        assert (await client.post("/api/auth/status")).json() == {
            "setup_required": True
        }
        await _bootstrap(client)
        assert (await client.post("/api/auth/status")).json() == {
            "setup_required": False
        }


async def test_bootstrap_sets_cookie_and_identifies(
    app_and_factory,
) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        response = await client.post(
            "/api/auth/bootstrap",
            json={"username": "alice", "password": "pw", "display_name": "Alice"},
        )
        set_cookie = response.headers["set-cookie"]
        assert "octave_session=" in set_cookie

        me = await client.get("/api/auth/me")
        assert me.status_code == 200
        body = me.json()
        assert body["username"] == "alice"
        assert body["role"] == "owner"

    # DB stored the hash, not the raw token
    async with factory() as session:
        row = (await session.execute(select(AuthSession))).scalars().one()
        assert row.id not in set_cookie


async def test_bootstrap_cookie_attributes(tmp_path: Path) -> None:
    app2, _ = await _auth_app(tmp_path / "secure", cookie_secure=True)
    async with await _client(app2) as client2:
        response = await client2.post(
            "/api/auth/bootstrap",
            json={"username": "alice", "password": "pw", "display_name": "Alice"},
        )
    raw_cookie = response.headers["set-cookie"]
    assert "octave_session=" in raw_cookie
    assert "HttpOnly" in raw_cookie
    assert "SameSite=lax" in raw_cookie
    assert "Path=/" in raw_cookie
    assert "Max-Age=" in raw_cookie
    assert "Secure" in raw_cookie  # only for the secure app


async def test_bootstrap_twice_409(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        response = await client.post(
            "/api/auth/bootstrap",
            json={"username": "bob", "password": "pw", "display_name": "Bob"},
        )
        assert response.status_code == 409


async def test_bootstrap_rejects_bad_username(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        response = await client.post(
            "/api/auth/bootstrap",
            json={"username": "A!", "password": "pw", "display_name": "X"},
        )
        assert response.status_code == 422


# -- login / logout -----------------------------------------------------------


async def test_login_success_updates_last_login(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        await client.post("/api/auth/logout")
        response = await client.post(
            "/api/auth/login", json={"username": "alice", "password": "pw"}
        )
        assert response.status_code == 200
    async with factory() as session:
        user = (
            await session.execute(select(User).where(User.username == "alice"))
        ).scalar_one()
        assert user.last_login_at is not None


async def test_login_wrong_password_401_no_cookie(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        await client.post("/api/auth/logout")
        response = await client.post(
            "/api/auth/login", json={"username": "alice", "password": "nope"}
        )
        assert response.status_code == 401
        assert SESSION_COOKIE not in response.cookies


async def test_logout_revokes(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        await client.post("/api/auth/logout")
        assert (await client.get("/api/auth/me")).status_code == 401
    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(AuthSession))
        assert count == 0


async def test_expired_cookie_401(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        response = await client.post(
            "/api/auth/bootstrap",
            json={"username": "alice", "password": "pw", "display_name": "Alice"},
        )
        token = response.cookies[SESSION_COOKIE]
    async with factory() as session:
        row = (await session.execute(select(AuthSession))).scalars().one()
        row.expires_at = utcnow() - timedelta(seconds=1)
        await session.commit()
    async with await _client(app) as client:
        client.cookies.set(SESSION_COOKIE, token)
        assert (await client.get("/api/auth/me")).status_code == 401


async def test_login_rate_limited(app_and_factory) -> None:
    """5 failures / 15 min per (username, client) -> 429. Advisory in-process
    limiter (spec: not a security control against determined attackers)."""
    from octave.routes.auth import _reset_rate_limiter

    app, _ = app_and_factory
    _reset_rate_limiter()
    async with await _client(app) as client:
        await _bootstrap(client)
        await client.post("/api/auth/logout")
        codes = [
            (
                await client.post(
                    "/api/auth/login",
                    json={"username": "alice", "password": "wrong"},
                )
            ).status_code
            for _ in range(6)
        ]
        assert codes[:5] == [401] * 5
        assert codes[5] == 429
    _reset_rate_limiter()


# -- admin ---------------------------------------------------------------------


async def test_admin_endpoints_role_gates(app_and_factory) -> None:
    app, factory = app_and_factory
    async with await _client(app) as client:
        # anonymous
        assert (await client.get("/api/auth/users")).status_code == 401

        await _bootstrap(client)  # now logged in as owner
        assert (await client.get("/api/auth/users")).status_code == 200

        created = await client.post(
            "/api/auth/users",
            json={"username": "bob", "password": "pw", "display_name": "Bob"},
        )
        assert created.status_code == 201
        bob_id = created.json()["id"]
    # member cannot administer
    async with await _client(app) as member:
        await member.post("/api/auth/login", json={"username": "bob", "password": "pw"})
        assert (await member.get("/api/auth/users")).status_code == 403
        assert (
            await member.patch(
                f"/api/auth/users/{bob_id}", json={"status": "deactivated"}
            )
        ).status_code == 403


async def test_last_owner_guard_via_patch(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        me = (await client.get("/api/auth/me")).json()
        demote = await client.patch(
            f"/api/auth/users/{me['id']}", json={"role": "member"}
        )
        assert demote.status_code == 409
        deactivate = await client.patch(
            f"/api/auth/users/{me['id']}", json={"status": "deactivated"}
        )
        assert deactivate.status_code == 409


async def test_patch_unknown_user_404(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        response = await client.patch(
            "/api/auth/users/nope", json={"status": "deactivated"}
        )
        assert response.status_code == 404


# -- guard: no route reachable anonymously --------------------------------------

# Spec (Testing): every route outside an *explicit* public allowlist must
# depend on get_current_user. The allowlist is ours to compose: login and
# logout are the identity-establishing endpoints themselves, and /ws
# authenticates inside its handshake handler (Task 5, close 4401/4403).
PUBLIC_PATHS = {
    "/api/auth/status",
    "/api/auth/bootstrap",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/health",
    "/api/version",
    "/ws",
}
DOC_PREFIXES = ("/docs", "/openapi.json", "/redoc")


def _depends_on_current_user(dependant) -> bool:
    if dependant.call is get_current_user:
        return True
    return any(_depends_on_current_user(sub) for sub in dependant.dependencies)


def test_every_non_public_route_requires_identity() -> None:
    """Regression gate: any new route without ``get_current_user`` fails."""
    offenders = []
    for route in global_app.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        if path in PUBLIC_PATHS or path.startswith(DOC_PREFIXES):
            continue
        dependant = getattr(route, "dependant", None)
        if dependant is None:  # websocket routes authenticate in-handler
            continue
        if not _depends_on_current_user(dependant):
            offenders.append(path)
    assert offenders == []
