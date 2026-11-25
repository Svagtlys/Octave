"""AuthStore: never commits; expiry and username rules live here."""

from datetime import timedelta

import pytest
from sqlalchemy import select
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


async def test_get_by_token_hash_expired_returns_none_row_survives(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        session.add(
            AuthSession(
                id="hx", user_id=user.id,
                created_at=now - timedelta(days=100),
                expires_at=now - timedelta(days=1),
                last_seen_at=now - timedelta(days=100),
            )
        )
        await session.commit()
    async with session_factory() as session:
        assert await AuthStore(session).get_by_token_hash("hx", now) is None
        await session.rollback()
    async with session_factory() as session:
        assert await session.get(AuthSession, "hx") is not None  # sweep is login's job


async def test_touch_by_hash_slides_expiry(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        await store.create_session(
            user_id=user.id, token_hash="h1", now=now - timedelta(hours=2)
        )
        await session.commit()
    async with session_factory() as session:
        count = await AuthStore(session).touch_session_by_hash("h1", now=now)
        await session.commit()
        assert count == 1
        row = await session.get(AuthSession, "h1")
        assert row is not None
        assert row.last_seen_at == now
        assert row.expires_at == now + timedelta(days=14)


async def test_touch_by_hash_bounded_by_absolute_cap(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # SQLite strftime truncates to milliseconds; aligning `now` makes the
    # SQL-computed cap round-trip exactly through the DATETIME processor.
    now = utcnow().replace(microsecond=0)
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        session.add(
            AuthSession(
                id="hc", user_id=user.id,
                created_at=now - timedelta(days=89),
                expires_at=now + timedelta(days=1),
                last_seen_at=now - timedelta(days=2),
            )
        )
        await session.commit()
    async with session_factory() as session:
        count = await AuthStore(session).touch_session_by_hash("hc", now=now)
        await session.commit()
        assert count == 1
        row = await session.get(AuthSession, "hc")
        assert row is not None
        # created_at + 90d beats now + 14d here; strftime keeps microseconds
        assert row.expires_at == now - timedelta(days=89) + timedelta(days=90)


async def test_touch_by_hash_throttled_within_grace(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    now = utcnow()
    async with session_factory() as session:
        store = AuthStore(session)
        user = await _make_user(store)
        await store.create_session(
            user_id=user.id, token_hash="hg", now=now - timedelta(hours=2)
        )
        # simulate a touch 30 s ago
        row = await session.get(AuthSession, "hg")
        assert row is not None
        row.last_seen_at = now - timedelta(seconds=30)
        await session.commit()
    async with session_factory() as session:
        count = await AuthStore(session).touch_session_by_hash("hg", now=now)
        await session.commit()
        assert count == 0
        row = await session.get(AuthSession, "hg")
        assert row is not None
        assert row.last_seen_at == now - timedelta(seconds=30)


async def test_touch_by_hash_unknown_hash_returns_zero(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await AuthStore(session).touch_session_by_hash("nope", now=utcnow()) == 0


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
