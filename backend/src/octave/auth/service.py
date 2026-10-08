"""AuthService: use-cases that compose AuthStore + hasher + settings.

The two seams the spec pins (Decision 3) are ``provision_user`` and
``issue_session`` — every future flow (OIDC, admin invite) routes user and
session creation through them. The service never commits; routes own the
transaction. All failures are :class:`octave.auth.errors.AuthError`
subclasses so HTTP and WS map them in one place.
"""

from __future__ import annotations

from datetime import datetime

from octave.auth.config import AuthSettings
from octave.auth.errors import (
    AccountDisabled,
    AlreadyBootstrapped,
    InvalidCredentials,
    NotLastOwnerGuard,
    UserNotFound,
)
from octave.auth.passwords import PasswordHasher
from octave.auth.store import AuthStore
from octave.auth.tokens import generate_token, hash_token
from octave.db.models import User
from octave.db.models.auth import AuthSession
from octave.db.models.base import utcnow
from octave.db.types import UserRole, UserStatus

__all__ = ["AuthService", "ResolvedSession"]

ResolvedSession = tuple[User, AuthSession]

# Verify-on-unknown runs against this decoy so a missing user costs the same
# work as a wrong password — login timing must not leak existence.
_DECOY_HASH = "argon2-decoy-hash-for-timing-equalization"


class AuthService:
    """Store + hasher + policy. Constructed per-request by deps."""

    def __init__(
        self,
        *,
        store: AuthStore,
        hasher: PasswordHasher,
        settings: AuthSettings,
    ) -> None:
        self._store = store
        self._hasher = hasher
        self._settings = settings

    # -- seams (spec Decision 3) -----------------------------------------

    async def provision_user(
        self,
        *,
        username: str,
        display_name: str,
        password_hash: str | None = None,
        role: UserRole = UserRole.MEMBER,
    ) -> User:
        """The single user-creation path (bootstrap, admin, future OIDC)."""
        return await self._store.create_user(
            username=username,
            display_name=display_name,
            password_hash=password_hash,
            role=role.value if isinstance(role, UserRole) else role,
        )

    async def issue_session(self, user_id: str, *, now: datetime) -> str:
        """The single session-creation path. Returns the RAW token once —
        only its hash is stored."""
        raw = generate_token()
        await self._store.create_session(
            user_id=user_id,
            token_hash=hash_token(raw),
            now=now,
            idle_ttl_days=self._settings.idle_ttl_days,
            absolute_ttl_days=self._settings.absolute_ttl_days,
        )
        return raw

    # -- use-cases ---------------------------------------------------------

    async def bootstrap_owner(
        self, username: str, password: str, display_name: str
    ) -> str:
        """First-run: create the single owner + session. Closes forever on
        success — there is no second bootstrap, ever."""
        if await self._store.user_exists():
            raise AlreadyBootstrapped
        user = await self.provision_user(
            username=username,
            display_name=display_name,
            password_hash=self._hasher.hash(password),
            role=UserRole.OWNER,
        )
        return await self.issue_session(user.id, now=utcnow())

    async def login(self, username: str, password: str) -> str:
        """Password login. Returns the raw session token (cookie value).

        All credential failures collapse to :class:`InvalidCredentials`;
        ``last_login_at`` is only touched on success.
        """
        now = utcnow()
        # Opportunistic sweep: login is the only place expired rows die
        # (spec: no background task; the read-path handles the rest).
        await self._store.purge_expired(now)
        user = await self._store.get_by_username(username)
        if user is None or user.password_hash is None:
            # Same shape as a wrong password: burn a verify, fail the same.
            self._hasher.verify(_DECOY_HASH, password)
            raise InvalidCredentials
        if not self._hasher.verify(user.password_hash, password):
            raise InvalidCredentials
        if user.status != UserStatus.ACTIVE:
            raise AccountDisabled
        if self._hasher.needs_rehash(user.password_hash):
            await self._store.set_password_hash(
                user, self._hasher.hash(password)
            )
        token = await self.issue_session(user.id, now=now)
        user.last_login_at = now
        return token

    async def logout(self, token: str) -> None:
        """Drop the session row (idempotent — no error if already gone)."""
        await self._store.delete_session_by_hash(hash_token(token))

    async def resolve_session(self, token: str) -> ResolvedSession | None:
        """Cookie → identity. ``None`` = anonymous (unknown/expired/disabled).
        Valid hits slide the idle expiry."""
        now = utcnow()
        row = await self._store.get_by_token_hash(hash_token(token), now)
        if row is None:
            return None
        user = await self._store.get_user_by_id(row.user_id)
        if user is None or user.status != UserStatus.ACTIVE:
            return None
        await self._store.touch_session(
            row,
            now=now,
            idle_ttl_days=self._settings.idle_ttl_days,
            absolute_ttl_days=self._settings.absolute_ttl_days,
        )
        return user, row

    # -- administration -----------------------------------------------------

    async def set_role(self, user_id: str, role: str, *, actor_id: str) -> None:
        """Role change with the lockout guard: never demote the last owner."""
        user = await self._store.get_user_by_id(user_id)
        if user is None:
            raise UserNotFound(user_id)
        if (
            role != UserRole.OWNER
            and user.role == UserRole.OWNER
            and await self._store.count_owners() <= 1
        ):
            raise NotLastOwnerGuard
        await self._store.set_role(user, role)

    async def admin_create_user(
        self, *, username: str, password: str, display_name: str
    ) -> User:
        """Owner-initiated registration: provision_user seam + a password
        credential set through the same hasher (never stored raw)."""
        user = await self.provision_user(username=username, display_name=display_name)
        await self._store.set_password_hash(user, self._hasher.hash(password))
        return user

    async def deactivate_user(
        self, user_id: str, *, actor_id: str, force: bool = False
    ) -> None:
        """Deactivate + instant logout everywhere (sessions deleted).

        Guards: the last owner cannot be deactivated (``force`` overrides
        only for break-glass CLI use), and the acting owner cannot
        self-deactivate without it.
        """
        user = await self._store.get_user_by_id(user_id)
        if user is None:
            raise UserNotFound(user_id)
        if not force:
            if user_id == actor_id:
                raise NotLastOwnerGuard
            if (
                user.role == UserRole.OWNER
                and await self._store.count_owners() <= 1
            ):
                raise NotLastOwnerGuard
        await self._store.set_status(user, UserStatus.DEACTIVATED.value)
        await self._store.delete_sessions_for_user(user_id)
