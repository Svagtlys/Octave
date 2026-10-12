"""EventStore: gap-free transcript appends (design spec 2026-09-28).

Real SQLite via session_factory; the uq_events_session_seq constraint is
the backstop for the seq race, exercised via a scripted stale-read subclass.
"""

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import octave.db as db_pkg
from octave.db.event_store import EventStore
from octave.db.models import Participant, Session, User
from octave.db.types import EventKind


async def _seed(session_factory: async_sessionmaker[AsyncSession]) -> str:
    """User u_1, sessions s_1/s_2, and a user participant (author FK)."""
    async with session_factory() as session:
        session.add(User(id="u_1", username="alice", display_name="Alice"))
        session.add(Session(id="s_1", created_by_user_id="u_1", status="active"))
        session.add(Session(id="s_2", created_by_user_id="u_1", status="active"))
        participant = Participant(id="p_u1", user_id="u_1", label="Alice")
        session.add(participant)
        await session.commit()
    return "p_u1"


async def _append(
    session_factory: async_sessionmaker[AsyncSession],
    session_id: str,
    author_id: str,
    content: str,
) -> int:
    async with session_factory() as session:
        event = await EventStore(session).append(
            session_id,
            EventKind.USER_MESSAGE,
            author_participant_id=author_id,
            payload={"content": content},
        )
        await session.commit()
        return event.seq


async def test_seq_starts_at_one_and_increments(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    author = await _seed(session_factory)
    assert await _append(session_factory, "s_1", author, "one") == 1
    assert await _append(session_factory, "s_1", author, "two") == 2


async def test_seq_scoped_per_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    author = await _seed(session_factory)
    assert await _append(session_factory, "s_1", author, "a") == 1
    assert await _append(session_factory, "s_2", author, "b") == 1
    assert await _append(session_factory, "s_1", author, "c") == 2


async def test_read_orders_and_windows(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    author = await _seed(session_factory)
    for text in ("one", "two", "three"):
        await _append(session_factory, "s_1", author, text)
    async with session_factory() as session:
        store = EventStore(session)
        everything = await store.read("s_1")
        tail = await store.read("s_1", after_seq=2)
    assert [e.seq for e in everything] == [1, 2, 3]
    assert [e.payload["content"] for e in everything] == ["one", "two", "three"]
    assert [e.seq for e in tail] == [3]


async def test_validates_user_message_payload(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    author = await _seed(session_factory)
    async with session_factory() as session:
        with pytest.raises(ValidationError):
            await EventStore(session).append(
                "s_1", EventKind.USER_MESSAGE, author_participant_id=author, payload={}
            )


async def test_assistant_payload_defaults_model_name_null(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    author = await _seed(session_factory)
    async with session_factory() as session:
        event = await EventStore(session).append(
            "s_1",
            EventKind.ASSISTANT_MESSAGE,
            author_participant_id=author,
            payload={"content": "hi"},
        )
        assert event.payload == {"content": "hi", "model_name": None}


async def test_tool_and_system_kinds_pass_through(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed(session_factory)
        event = await EventStore(session).append(
            "s_1", EventKind.TOOL_CALL, payload={"server_id": "s", "tool": "t"}
        )
        assert event.payload == {"server_id": "s", "tool": "t"}


async def test_never_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    async with session_factory() as session:
        await EventStore(session).append(
            "s_1", EventKind.SYSTEM, payload={"content": "uncommitted"}
        )
    async with session_factory() as other:
        assert await EventStore(other).read("s_1") == []  # not committed yet
    async with session_factory() as session:
        await EventStore(session).append(
            "s_1", EventKind.SYSTEM, payload={"content": "committed"}
        )
        await session.commit()
    async with session_factory() as other:
        rows = await EventStore(other).read("s_1")
    assert [e.payload["content"] for e in rows] == ["committed"]


class _StaleReadStore(EventStore):
    """Scripts stale seq reads to exercise the IntegrityError retry path:
    real cross-connection races are invisible to a stale SQLite snapshot,
    so the retry loop is tested at its seam."""

    def __init__(self, session: AsyncSession, stale: list[int]) -> None:
        super().__init__(session)
        self._stale = stale

    async def _next_seq(self, session_id: str) -> int:
        if self._stale:
            return self._stale.pop(0)
        return await super()._next_seq(session_id)


async def test_retry_recovers_from_seq_conflict(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    async with session_factory() as winner:
        await EventStore(winner).append("s_1", EventKind.SYSTEM, payload={"c": 1})
        await winner.commit()  # committed seq=1
    async with session_factory() as loser:
        store = _StaleReadStore(loser, stale=[1])  # stale read -> collides
        event = await store.append("s_1", EventKind.SYSTEM, payload={"c": 2})
        assert event.seq == 2  # retry recomputed from fresh max
        await loser.commit()


async def test_exported_from_package() -> None:
    assert hasattr(db_pkg, "EventStore")
    assert "EventStore" in db_pkg.__all__
