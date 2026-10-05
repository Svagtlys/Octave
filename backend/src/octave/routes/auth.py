"""Auth HTTP surface (issue #122).

Endpoint vocabulary per the spec: status/bootstrap are public; login and
logout establish/end a session; ``me`` is the frontend identity probe; the
users endpoints are owner-only admin. Routes own commits (deps compose
``get_db_session``); service errors propagate as ``AuthError`` and map to
JSON once via ``octave.middleware`` (``http_status_of``).

Rate limiting is an in-process advisory dict (username, client host) —
5 failures / 15 minutes -> 429. It raises the cost of scripted guessing
against this single process; it is NOT a control against a determined
attacker, and it resets on restart.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from octave.auth.deps import (
    SESSION_COOKIE,
    get_auth_service,
    get_current_user,
    require_owner,
)
from octave.auth.errors import AuthError, http_status_of
from octave.auth.service import AuthService
from octave.auth.store import AuthStore
from octave.db.deps import get_db_session
from octave.db.models import User
from octave.db.types import UserRole, UserStatus

__all__ = ["router"]

router = APIRouter(prefix="/auth", tags=["auth"])

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60

_failures: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def _reset_rate_limiter() -> None:
    """Test seam: drop all recorded failures."""
    _failures.clear()


def _key(request: Request, username: str) -> tuple[str, str]:
    return (username.lower(), request.client.host if request.client else "unknown")


def _check_rate_limit(request: Request, username: str) -> None:
    window = _failures[_key(request, username)]
    now = time.monotonic()
    while window and now - window[0] > WINDOW_SECONDS:
        window.popleft()
    if len(window) >= MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429, detail="Too many failed attempts; try again later"
        )


def _set_session_cookie(response: Response, token: str, request: Request) -> None:
    settings = getattr(request.app.state, "auth_settings", None)
    if settings is None:
        from octave.auth.config import AuthSettings

        settings = AuthSettings()
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        max_age=settings.idle_ttl_days * 86400,
    )


class _Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=1)


class _CreateUser(BaseModel):
    username: str = Field(pattern=r"^[a-z0-9._-]{2,32}$")
    password: str = Field(min_length=1)
    display_name: str = Field(min_length=1)


class _Bootstrap(_CreateUser):
    pass


class _PatchUser(BaseModel):
    role: UserRole | None = None
    status: UserStatus | None = None


def _identity(user: User) -> dict[str, str]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
    }


@router.post("/status")
async def auth_status(
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, bool]:
    """Public: does a first-run bootstrap need to happen?"""
    exists = await AuthStore(session).user_exists()
    return {"setup_required": not exists}


@router.post("/bootstrap", status_code=201)
async def bootstrap(
    body: _Bootstrap,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    service: AuthService = Depends(get_auth_service),
) -> dict[str, str]:
    """First-run owner creation. Closes forever once any user exists."""
    token = await service.bootstrap_owner(
        body.username, body.password, body.display_name
    )
    await session.commit()
    _set_session_cookie(response, token, request)
    user = await AuthStore(session).get_by_username(body.username)
    assert user is not None  # just created
    return _identity(user)


@router.post("/login")
async def login(
    body: _Credentials,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    service: AuthService = Depends(get_auth_service),
) -> dict[str, bool]:
    """Password login. Credential failures collapse to 401 (middleware
    maps the AuthError) and feed the advisory rate limiter."""
    _check_rate_limit(request, body.username)
    try:
        token = await service.login(body.username, body.password)
    except AuthError as exc:
        _failures[_key(request, body.username)].append(time.monotonic())
        # One message for every credential failure shape — never reveal which.
        raise HTTPException(
            status_code=http_status_of(exc), detail="Invalid credentials"
        ) from exc
    await session.commit()
    _failures.pop(_key(request, body.username), None)
    _set_session_cookie(response, token, request)
    return {"ok": True}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db_session),
    service: AuthService = Depends(get_auth_service),
) -> dict[str, bool]:
    """Delete the session row (idempotent) and clear the cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await service.logout(token)
    await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: Annotated[User, Depends(get_current_user)]) -> dict[str, str]:
    """Current identity for the frontend (session-expiry probe)."""
    return _identity(user)


@router.get("/users")
async def list_users(
    _owner: Annotated[User, Depends(require_owner)],
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, str]]:
    users = await AuthStore(session).list_users()
    return [_identity(user) for user in users]


@router.post("/users", status_code=201)
async def create_user(
    body: _CreateUser,
    _owner: Annotated[User, Depends(require_owner)],
    session: AsyncSession = Depends(get_db_session),
    service: AuthService = Depends(get_auth_service),
) -> dict[str, str]:
    """Owner-only registration seam (future: gated by a flag)."""
    user = await service.admin_create_user(
        username=body.username, password=body.password, display_name=body.display_name
    )
    await session.commit()
    return _identity(user)


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    body: _PatchUser,
    actor: Annotated[User, Depends(require_owner)],
    session: AsyncSession = Depends(get_db_session),
    service: AuthService = Depends(get_auth_service),
) -> dict[str, str]:
    """Role/status change with last-owner + self-deactivation guards
    (service-side; guard violations raise NotLastOwnerGuard -> 409)."""
    if body.role is not None:
        await service.set_role(user_id, body.role.value, actor_id=actor.id)
    if body.status == UserStatus.DEACTIVATED:
        await service.deactivate_user(user_id, actor_id=actor.id)
    await session.commit()
    user = await AuthStore(session).get_user_by_id(user_id)
    assert user is not None
    return _identity(user)
