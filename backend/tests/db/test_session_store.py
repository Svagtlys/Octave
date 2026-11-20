"""SessionStore tests on real SQLite (issue #121).

Shared engine/session_factory fixtures: temp-file SQLite, constraints live.
Timestamps are set explicitly so ordering never depends on wall-clock
resolution. Events are appended through EventStore (the canonical write path),
then re-timed for determinism.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.db.event_store import EventStore
from octave.db.models import Event, Participant, Session, SessionParticipant, User
from octave.db.session_store import SessionStore
from octave.db.types import EventKind


def _dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=timezone.utc)


async def _seed_user(
    session: AsyncSession, *, user_id: str, participant_id: str, display_name: str
) -> None:
    session.add(User(id=user_id, username=user_id, display_name=display_name))
    session.add(Participant(id=participant_id, user_id=user_id, label=display_name))
    await session.flush()


async def _make_session(
    session: AsyncSession,
    *,
    session_id: str,
    owner_user_id: str,
    created_at: datetime,
    title: str | None = None,
) -> Session:
    row = Session(
        id=session_id,
        created_by_user_id=owner_user_id,
        status="active",
        title=title,
        created_at=created_at,
    )
    session.add(row)
    await session.flush()
    return row


async def _append(
    session: AsyncSession,
    session_id: str,
    kind: EventKind,
    content: str,
    *,
    author: str | None = None,
    created_at: datetime | None = None,
) -> Event:
    event = await EventStore(session).append(
        session_id, kind, author_participant_id=author, payload={"content": content}
    )
    if created_at is not None:
        event.created_at = created_at
        await session.flush()
    return event


# -- participant lookup ---------------------------------------------------------


async def test_get_participant_for_user_hit_and_miss(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        store = SessionStore(session)
        found = await store.get_participant_for_user("u_1")
        assert found is not None
        assert found.id == "p_1"
        assert await store.get_participant_for_user("u_ghost") is None


# -- create_session ---------------------------------------------------------------


async def test_create_session_persists_session_and_owner_membership(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        row = await SessionStore(session).create_session(
            owner_user_id="u_1", owner_participant_id="p_1", title="Planning"
        )
        await session.commit()
        session_id = row.id
        assert len(session_id) == 32  # uuid4 hex

    async with session_factory() as check:
        loaded = await check.get(Session, session_id)
        assert loaded is not None
        assert loaded.status == "active"
        assert loaded.title == "Planning"
        assert loaded.created_by_user_id == "u_1"
        membership = (
            await check.execute(select(SessionParticipant))
        ).scalars().one()
        assert membership.session_id == session_id
        assert membership.participant_id == "p_1"
        assert membership.role == "speaker"
        assert membership.left_at is None


async def test_create_session_title_optional(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        row = await SessionStore(session).create_session(
            owner_user_id="u_1", owner_participant_id="p_1", title=None
        )
        await session.commit()
        session_id = row.id
    async with session_factory() as check:
        loaded = await check.get(Session, session_id)
        assert loaded is not None
        assert loaded.title is None


async def test_create_session_never_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An uncommitted create is invisible to a second session (store contract)."""
    async with session_factory() as session:
        await _seed_user(session, user_id="u_1", participant_id="p_1", display_name="Alice")
        await SessionStore(session).create_session(
            owner_user_id="u_1", owner_participant_id="p_1", title="Quiet"
        )
        # no commit
    async with session_factory() as check:
        count = await check.scalar(select(func.count()).select_from(Session))
        assert count == 0
