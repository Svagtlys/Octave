"""AuthService: bootstrap/login/deactivate/resolve — dummy hasher, real DB."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.auth.config import AuthSettings
from octave.auth.errors import (
    AccountDisabled,
    AlreadyBootstrapped,
    InvalidCredentials,
    NotLastOwnerGuard,
)
from octave.auth.passwords import DummyHasher
from octave.auth.service import AuthService
from octave.auth.store import AuthStore
from octave.db.models import Participant
from octave.db.models.auth import AuthSession
from octave.db.models.base import utcnow


def _service(
    session: AsyncSession,
    *,
    idle_days: int = 14,
    absolute_days: int = 90,
) -> AuthService:
    settings = AuthSettings(
        idle_ttl_days=idle_days, absolute_ttl_days=absolute_days
    )
    return AuthService(
        store=AuthStore(session), hasher=DummyHasher(), settings=settings
    )


async def test_bootstrap_creates_owner_participant_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        token = await _service(session).bootstrap_owner(
            username="Alice", password="pw", display_name="Alice"
        )
        await session.commit()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await store.get_by_username("alice")
        assert user is not None and user.role == "owner"
        assert user.password_hash == "dummy$pw"
        rows = (await session.execute(select(AuthSession))).scalars().all()
        assert len(rows) == 1
        assert rows[0].id != token  # hash stored, not raw
        participants = (
            await session.execute(select(Participant))
        ).scalars().all()
        assert len(participants) == 1


async def test_bootstrap_second_call_refused(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _service(session).bootstrap_owner("alice", "pw", "Alice")
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(AlreadyBootstrapped):
            await _service(session).bootstrap_owner("bob", "pw", "Bob")


async def test_bootstrap_refused_when_any_user_exists(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The bootstrap gate is ``users`` non-empty, not username collision —
    a pre-existing member (OIDC backfill, say) closes the door just like a
    prior owner would. UsernameTaken lives on the admin-creation path."""
    async with session_factory() as session:
        store = AuthStore(session)
        await store.create_user(username="alice", display_name="Existing")
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(AlreadyBootstrapped):
            await _service(session).bootstrap_owner("bob", "pw", "Bob")


async def test_login_valid_returns_raw_token_and_updates_last_login(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _service(session).bootstrap_owner("alice", "pw", "Alice")
        await session.commit()
    async with session_factory() as session:
        token = await _service(session).login("ALICE", "pw")
        await session.commit()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await store.get_by_username("alice")
        assert user is not None and user.last_login_at is not None
        resolved = await _service(session).resolve_session(token)
        assert resolved is not None
        user2, _sess = resolved
        assert user2.username == "alice"


async def test_login_wrong_password_invalid_credentials(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _service(session).bootstrap_owner("alice", "pw", "Alice")
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(InvalidCredentials):
            await _service(session).login("alice", "nope")
        await session.rollback()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await store.get_by_username("alice")
        assert user is not None and user.last_login_at is None


async def test_login_unknown_username_same_error_profile(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Unknown username must run the same code path (dummy-verify on a
    decoy hash) so response timing never leaks existence."""
    async with session_factory() as session:
        await _service(session).bootstrap_owner("alice", "pw", "Alice")
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(InvalidCredentials):
            await _service(session).login("nobody", "pw")


async def test_login_null_password_hash_fails_closed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = AuthStore(session)
        await store.create_user(username="ghost", display_name="Ghost")
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(InvalidCredentials):
            await _service(session).login("ghost", "anything")


async def test_login_deactivated_refused(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _service(session).bootstrap_owner("alice", "pw", "Alice")
        await session.commit()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await store.get_by_username("alice")
        assert user is not None
        await store.set_status(user, "deactivated")
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(AccountDisabled):
            await _service(session).login("alice", "pw")


async def test_deactivate_deletes_sessions_and_guards_last_owner(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _service(session).bootstrap_owner("alice", "pw", "Alice")
        await session.commit()
    async with session_factory() as session:
        service = _service(session)
        await service.login("alice", "pw")
        await session.commit()
    async with session_factory() as session:
        service = _service(session)
        store = AuthStore(session)
        alice = await store.get_by_username("alice")
        assert alice is not None
        # self-deactivation of the acting owner: refused
        with pytest.raises(NotLastOwnerGuard):
            await service.deactivate_user(alice.id, actor_id=alice.id)
        # and demoting/deactivating the last owner at all: refused
        with pytest.raises(NotLastOwnerGuard):
            await service.set_role(alice.id, "member", actor_id=alice.id)
    async with session_factory() as session:
        service = _service(session)
        store = AuthStore(session)
        alice = await store.get_by_username("alice")
        assert alice is not None
        await service.deactivate_user(alice.id, actor_id=alice.id, force=True)
        await session.commit()
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(AuthSession)) == 0
        user = await AuthStore(session).get_by_username("alice")
        assert user is not None and user.status == "deactivated"


async def test_deactivate_other_owner_with_second_owner(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        service = _service(session)
        await service.bootstrap_owner("alice", "pw", "Alice")
        store = AuthStore(session)
        bob = await store.create_user(username="bob", display_name="Bob")
        await store.set_role(bob, "owner")
        await session.commit()
    async with session_factory() as session:
        service = _service(session)
        store = AuthStore(session)
        alice = await store.get_by_username("alice")
        assert alice is not None
        await service.deactivate_user(bob.id, actor_id=alice.id)
        await session.commit()
    async with session_factory() as session:
        store = AuthStore(session)
        bob = await store.get_by_username("bob")
        assert bob is not None and bob.status == "deactivated"


async def test_resolve_expired_returns_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        token = await _service(session).bootstrap_owner("alice", "pw", "Alice")
        await session.commit()
    async with session_factory() as session:
        row = (
            await session.execute(select(AuthSession))
        ).scalars().one()
        row.expires_at = utcnow() - timedelta(seconds=1)
        await session.commit()
    async with session_factory() as session:
        assert await _service(session).resolve_session(token) is None


async def test_resolve_unknown_token_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await _service(session).resolve_session("nope") is None


async def test_login_purges_expired_rows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _service(session).bootstrap_owner("alice", "pw", "Alice")
        await session.commit()
    async with session_factory() as session:
        rows = (
            await session.execute(select(AuthSession))
        ).scalars().all()
        rows[0].expires_at = utcnow() - timedelta(seconds=1)
        await session.commit()
    async with session_factory() as session:
        await _service(session).login("alice", "pw")
        await session.commit()
    async with session_factory() as session:
        # login purge removed the expired one; the fresh login left exactly one
        assert await session.scalar(
            select(func.count()).select_from(AuthSession)
        ) == 1
