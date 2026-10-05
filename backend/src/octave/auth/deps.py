"""FastAPI dependencies: cookie -> identity, role gates.

Composition contract (spec Decision 5): ``get_current_user`` rides
``get_db_session``, never replaces it — routes still own commits. The
hasher/settings come from ``app.state`` so tests wire :class:`DummyHasher`
without monkeypatching; production defaults to argon2 + env settings.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from octave.auth.config import AuthSettings
from octave.auth.passwords import Argon2Hasher, PasswordHasher
from octave.auth.service import AuthService
from octave.auth.store import AuthStore
from octave.db.deps import get_db_session
from octave.db.models import User
from octave.db.types import UserRole

__all__ = [
    "SESSION_COOKIE",
    "get_auth_service",
    "get_current_user",
    "require_owner",
]

SESSION_COOKIE = "octave_session"
"""Sole credential the browser carries. HttpOnly + SameSite=lax: lax blocks
cross-site POST delivery, which covers every mutating endpoint (CSRF note
in the spec)."""


def _hasher(request: Request) -> PasswordHasher:
    return getattr(request.app.state, "auth_hasher", None) or Argon2Hasher()


def _settings(request: Request) -> AuthSettings:
    return getattr(request.app.state, "auth_settings", None) or AuthSettings()


async def get_auth_service(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> AuthService:
    """One AuthService per request, riding the request's DB session."""
    return AuthService(
        store=AuthStore(session), hasher=_hasher(request), settings=_settings(request)
    )


async def get_current_user(
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> User:
    """Resolve the session cookie to an active user, or 401.

    Valid hits slide the idle expiry (inside ``resolve_session``). The
    expiry write stays uncommitted until the route commits — for pure reads
    that is fine: expiry is recomputed from ``last_seen_at`` on each use,
    and the sliding window is advisory-precision by design.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    resolved = await service.resolve_session(token)
    if resolved is None:
        raise HTTPException(status_code=401, detail="Session invalid or expired")
    return resolved[0]


async def require_owner(user: User = Depends(get_current_user)) -> User:
    """Account-administration gate (spec: role gates admin only — feature
    access is uniform for all authenticated users)."""
    if user.role != UserRole.OWNER:
        raise HTTPException(status_code=403, detail="Owner privileges required")
    return user
