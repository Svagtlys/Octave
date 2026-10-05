"""AuthStore: never commits; expiry and username rules live here."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.auth.errors import UsernameTaken
from octave.auth.store import AuthStore
from octave.auth.tokens import generate_token, hash_token
from octave.db.models import Participant, User
from octave.db.models.auth import AuthSession
from octave.db.models.base import utcnow

USERNAME = "alice"


async def _make_user(
    store: AuthStore, username: str = USERNAME, **kw: object
) -> User:
    return await store.create_user(
        username=username, display_name=str(kw.get("display_name", "Alice"))
    )


async def test_create_user_adds_participant_in_same_flush(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        await session.commit()
    async with session_factory() as session:
        participants = (
            await session.execute(
                select(Participant).where(Participant.user_id == user.id)
            )
        ).scalars().all()
        assert len(participants) == 1
        assert participants[0].label == user.display_name


async def test_create_user_lowercases_username(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user = await _make_user(AuthStore(session), username="Alice")
        await session.commit()
        assert user.username == "alice"


async def test_create_user_duplicate_raises_username_taken(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = AuthStore(session)
        await _make_user(store)
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(UsernameTaken):
            await AuthStore(session).create_user(
                username="Alice", display_name="Other"
            )


async def test_get_by_username_case_folds(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _make_user(AuthStore(session))
        await session.commit()
    async with session_factory() as session:
        store = AuthStore(session)
        assert await store.get_by_username("ALICE") is not None
        assert await store.get_by_username("nobody") is None


async def test_get_by_token_hash_valid(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        token = generate_token()
        await store.create_session(
            user_id=user.id, token_hash=hash_token(token), now=now
        )
        await session.commit()
    async with session_factory() as session:
        row = await AuthStore(session).get_by_token_hash(hash_token(token), now)
        assert row is not None and row.user_id == user.id


async def test_get_by_token_hash_expired_returns_none_and_deletes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        token = generate_token()
        row = AuthSession(
            id=hash_token(token),
            user_id=user.id,
            created_at=now - timedelta(days=100),
            expires_at=now - timedelta(days=1),
            last_seen_at=now - timedelta(days=100),
        )
        session.add(row)
        await session.commit()
    async with session_factory() as session:
        store = AuthStore(session)
        assert await store.get_by_token_hash(hash_token(token), now) is None
        await session.commit()
    async with session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(AuthSession)
        )
        assert count == 0


async def test_touch_session_slides_expiry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        token_row = await store.create_session(
            user_id=user.id, token_hash="h1", now=now - timedelta(hours=1)
        )
        await session.commit()
        token_row_id = token_row.id
    async with session_factory() as session:
        store = AuthStore(session)
        row = await store.get_by_token_hash(token_row_id, now)
        assert row is not None
        await store.touch_session(row, now=now)
        assert row.expires_at == now + timedelta(days=14)
        assert row.last_seen_at == now


async def test_touch_session_bounded_by_absolute_cap(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        row = AuthSession(
            id="hc",
            user_id=user.id,
            created_at=now - timedelta(days=89),
            expires_at=now + timedelta(days=1),
            last_seen_at=now - timedelta(days=1),
        )
        session.add(row)
        await session.commit()
    async with session_factory() as session:
        store = AuthStore(session)
        await store.touch_session(row, now=now)
        # created_at + 90d beats now + 14d here
        assert row.expires_at == now - timedelta(days=89) + timedelta(days=90)


async def test_delete_sessions_for_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        other = await _make_user(store, username="bob", display_name="Bob")
        await store.create_session(user_id=user.id, token_hash="ha", now=now)
        await store.create_session(user_id=other.id, token_hash="hb", now=now)
        await session.commit()
    async with session_factory() as session:
        store = AuthStore(session)
        deleted = await store.delete_sessions_for_user(user.id)
        await session.commit()
        assert deleted == 1
    async with session_factory() as session:
        remaining = (
            await session.execute(select(AuthSession))
        ).scalars().all()
        assert [row.id for row in remaining] == ["hb"]


async def test_role_status_password_and_counts(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        assert await store.count_owners() == 0
        assert await store.user_exists() is True
        await store.set_role(user, "owner")
        await store.set_password_hash(user, "dummy$pw")
        assert await store.count_owners() == 1
        await store.set_status(user, "deactivated")
        await session.commit()
    async with session_factory() as session:
        fresh = await AuthStore(session).get_by_username("alice")
        assert fresh is not None
        assert fresh.role == "owner"
        assert fresh.status == "deactivated"
        assert fresh.password_hash == "dummy$pw"


async def test_purge_expired(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        await store.create_session(user_id=user.id, token_hash="live", now=now)
        dead = AuthSession(
            id="dead",
            user_id=user.id,
            created_at=now - timedelta(days=100),
            expires_at=now - timedelta(seconds=1),
            last_seen_at=now - timedelta(days=100),
        )
        session.add(dead)
        await session.commit()
    async with session_factory() as session:
        purged = await AuthStore(session).purge_expired(now)
        await session.commit()
        assert purged == 1
