"""AuthStore: auth-plane persistence over one :class:`AsyncSession`.

Same contract as ``VaultStore`` / ``EventStore``: **never commits** — callers
own transaction boundaries (routes commit via ``get_db_session``). Expiry
computation lives here because both the service and the token lookup must
agree on it; policy values arrive from :class:`AuthSettings` via defaults
mirroring the settings defaults.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from octave.auth.errors import UsernameTaken
from octave.db.models import Participant, User
from octave.db.models.auth import AuthSession
from octave.db.types import UserRole, UserStatus

__all__ = ["AuthStore", "USERNAME_PATTERN"]

USERNAME_PATTERN = re.compile(r"^[a-z0-9._-]{2,32}$")
"""Server-side username shape (spec §Data model): lowercase, immutable."""


class AuthStore:
    """Persistence for users and ``auth_sessions`` rows. One session per
    instance; no internal state."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- users ---------------------------------------------------------

    async def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str | None = None,
        role: str = UserRole.MEMBER,
    ) -> User:
        """Insert the user and its ``Participant`` row in the same flush —
        a user that cannot speak is a lockout bug, so they are inseparable."""
        normalized = username.lower()
        if not USERNAME_PATTERN.match(normalized):
            raise ValueError(
                f"username must match [a-z0-9._-]{{2,32}}, got {username!r}"
            )
        if role not in set(UserRole):
            raise ValueError(f"unknown role {role!r}")
        if await self.get_by_username(normalized) is not None:
            raise UsernameTaken(username)
        user = User(
            id=uuid.uuid4().hex,
            username=normalized,
            password_hash=password_hash,
            display_name=display_name,
            role=role,
            status=UserStatus.ACTIVE,
        )
        self._session.add(user)
        self._session.add(
            Participant(id=uuid.uuid4().hex, user_id=user.id, label=display_name)
        )
        try:
            await self._session.flush()
        except IntegrityError as exc:  # race between check and insert
            raise UsernameTaken(username) from exc
        return user

    async def get_by_username(self, username: str) -> User | None:
        """Case-folded lookup: ``ALICE`` finds ``alice`` (login UX); storage
        stays lowercase."""
        result = await self._session.execute(
            select(User).where(func.lower(User.username) == username.lower())
        )
        return result.scalars().first()

    async def get_user_by_id(self, user_id: str) -> User | None:
        return await self._session.get(User, user_id)

    async def set_password_hash(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        await self._session.flush()

    async def set_role(self, user: User, role: str) -> None:
        if role not in set(UserRole):
            raise ValueError(f"unknown role {role!r}")
        user.role = role
        await self._session.flush()

    async def set_status(self, user: User, status: str) -> None:
        if status not in set(UserStatus):
            raise ValueError(f"unknown status {status!r}")
        user.status = status
        await self._session.flush()

    async def count_owners(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(User).where(User.role == UserRole.OWNER)
        )
        return int(result.scalar_one())

    async def user_exists(self) -> bool:
        """Any user at all — the bootstrap gate (first-run) and account
        administration sanity check."""
        result = await self._session.execute(
            select(func.count()).select_from(User)
        )
        return int(result.scalar_one()) > 0

    # -- sessions --------------------------------------------------------

    async def create_session(
        self,
        *,
        user_id: str,
        token_hash: str,
        now: datetime,
        idle_ttl_days: int = 14,
        absolute_ttl_days: int = 90,
    ) -> AuthSession:
        row = AuthSession(
            id=token_hash,
            user_id=user_id,
            created_at=now,
            expires_at=min(
                now + timedelta(days=idle_ttl_days),
                now + timedelta(days=absolute_ttl_days),
            ),
            last_seen_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def get_by_token_hash(
        self, token_hash: str, now: datetime
    ) -> AuthSession | None:
        """Valid rows only: an expired row is deleted on read (lazy sweep)
        and reported as absent."""
        row = await self._session.get(AuthSession, token_hash)
        if row is None:
            return None
        if row.expires_at <= now:
            await self._session.delete(row)
            await self._session.flush()
            return None
        return row

    async def touch_session(
        self,
        row: AuthSession,
        *,
        now: datetime,
        idle_ttl_days: int = 14,
        absolute_ttl_days: int = 90,
    ) -> None:
        """Slide idle expiry forward, never past the absolute cap
        (``created_at + absolute_ttl_days``)."""
        row.last_seen_at = now
        row.expires_at = min(
            now + timedelta(days=idle_ttl_days),
            row.created_at + timedelta(days=absolute_ttl_days),
        )
        await self._session.flush()

    async def delete_sessions_for_user(self, user_id: str) -> int:
        """Deactivation revocation: instant logout everywhere. Returns the
        number of rows deleted."""
        result = await self._session.execute(
            delete(AuthSession).where(AuthSession.user_id == user_id)
        )
        return int(_rowcount(result))

    async def delete_session_by_hash(self, token_hash: str) -> int:
        """Logout: drop one row. Idempotent — already-gone is not an error."""
        result = await self._session.execute(
            delete(AuthSession).where(AuthSession.id == token_hash)
        )
        return int(_rowcount(result))

    async def purge_expired(self, now: datetime) -> int:
        """Lazy sweep (run inside ``login`` only, per spec — no background
        task). Deletes rows whose ``expires_at`` has passed."""
        result = await self._session.execute(
            delete(AuthSession).where(AuthSession.expires_at < now)
        )
        return int(_rowcount(result))


def _rowcount(result: Any) -> int:
    """mypy-safe rowcount for DML (mirrors octave.agent.instances)."""
    return int(cast(CursorResult[Any], result).rowcount)
