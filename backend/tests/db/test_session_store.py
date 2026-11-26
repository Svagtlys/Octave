"""SessionStore tests on real SQLite (issue #121).

Shared engine/session_factory fixtures: temp-file SQLite, constraints live.
Timestamps are set explicitly so ordering never depends on wall-clock
resolution. Events are appended through EventStore (the canonical write path),
then re-timed for determinism.
"""

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.db.event_store import EventStore
from octave.db.models import (
    Agent,
    Event,
    Participant,
    Session,
    SessionParticipant,
    User,
)
from octave.db.session_store import SessionStore
from octave.db.types import EventKind


def _dt(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


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
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
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
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
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
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
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
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await SessionStore(session).create_session(
            owner_user_id="u_1", owner_participant_id="p_1", title="Quiet"
        )
        # no commit
    async with session_factory() as check:
        count = await check.scalar(select(func.count()).select_from(Session))
        assert count == 0


# -- list_sessions: ordering ------------------------------------------------------


async def test_orders_by_latest_event_with_created_at_fallback(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Sessions with events sort by MAX(events.created_at); eventless sessions
    fall back to sessions.created_at. Mixed list interleaves."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        for sid, day in [("s_a", 1), ("s_b", 2), ("s_c", 3)]:
            await _make_session(
                session, session_id=sid, owner_user_id="u_1", created_at=_dt(day)
            )
            await session.flush()
            member = SessionParticipant(session_id=sid, participant_id="p_1")
            session.add(member)
        await _append(
            session, "s_c", EventKind.USER_MESSAGE, "late", created_at=_dt(10)
        )
        await session.commit()

    async with session_factory() as session:
        rows, total = await SessionStore(session).list_sessions(
            participant_id="p_1", limit=10, offset=0
        )
        assert [r.id for r in rows] == ["s_c", "s_b", "s_a"]
        assert total == 3


async def test_tie_break_is_id_desc(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        for sid in ("s_a", "s_b"):
            await _make_session(
                session, session_id=sid, owner_user_id="u_1", created_at=_dt(5)
            )
            session.add(SessionParticipant(session_id=sid, participant_id="p_1"))
        await session.commit()

    async with session_factory() as session:
        rows, _ = await SessionStore(session).list_sessions(
            participant_id="p_1", limit=10, offset=0
        )
        assert [r.id for r in rows] == ["s_b", "s_a"]


# -- list_sessions: preview ---------------------------------------------------------


async def test_preview_picks_latest_message_ignoring_other_kinds(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Latest user/assistant message by seq wins; tool_call never previews but
    still counts for last_activity."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        session.add(SessionParticipant(session_id="s_1", participant_id="p_1"))
        await _append(
            session,
            "s_1",
            EventKind.USER_MESSAGE,
            "first",
            author="p_1",
            created_at=_dt(5),
        )
        await _append(
            session, "s_1", EventKind.ASSISTANT_MESSAGE, "second", created_at=_dt(6)
        )
        await _append(session, "s_1", EventKind.TOOL_CALL, "tool", created_at=_dt(7))
        await session.commit()

    async with session_factory() as session:
        rows, _ = await SessionStore(session).list_sessions(
            participant_id="p_1", limit=10, offset=0
        )
        assert rows[0].last_message == "second"
        assert rows[0].last_activity == _dt(7)


async def test_preview_null_without_message_events(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A session with only system events has activity but no preview."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        session.add(SessionParticipant(session_id="s_1", participant_id="p_1"))
        await _append(
            session, "s_1", EventKind.SYSTEM, "notice", created_at=_dt(4)
        )
        await session.commit()

    async with session_factory() as session:
        rows, _ = await SessionStore(session).list_sessions(
            participant_id="p_1", limit=10, offset=0
        )
        assert rows[0].last_message is None
        assert rows[0].last_activity == _dt(4)


# -- list_sessions: scoping + pagination ----------------------------------------


async def test_scoping_excludes_other_users_sessions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _seed_user(
            session, user_id="u_2", participant_id="p_2", display_name="Bob"
        )
        await _make_session(
            session, session_id="s_alice", owner_user_id="u_1", created_at=_dt(1)
        )
        session.add(SessionParticipant(session_id="s_alice", participant_id="p_1"))
        await _make_session(
            session, session_id="s_bob", owner_user_id="u_2", created_at=_dt(2)
        )
        session.add(SessionParticipant(session_id="s_bob", participant_id="p_2"))
        await session.commit()

    async with session_factory() as session:
        rows, total = await SessionStore(session).list_sessions(
            participant_id="p_1", limit=10, offset=0
        )
        assert [r.id for r in rows] == ["s_alice"]
        assert total == 1


async def test_left_membership_excluded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        session.add(
            SessionParticipant(
                session_id="s_1", participant_id="p_1", left_at=_dt(3)
            )
        )
        await session.commit()

    async with session_factory() as session:
        rows, total = await SessionStore(session).list_sessions(
            participant_id="p_1", limit=10, offset=0
        )
        assert rows == []
        assert total == 0


async def test_pagination_slices_and_total(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """total counts all current memberships regardless of page slice; offset
    past the end returns an empty page with the real total."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        for sid, day in [("s_1", 1), ("s_2", 2), ("s_3", 3)]:
            await _make_session(
                session, session_id=sid, owner_user_id="u_1", created_at=_dt(day)
            )
            session.add(SessionParticipant(session_id=sid, participant_id="p_1"))
        await session.commit()

    async with session_factory() as session:
        store = SessionStore(session)
        page1, total1 = await store.list_sessions(
            participant_id="p_1", limit=2, offset=0
        )
        assert [r.id for r in page1] == ["s_3", "s_2"]
        assert total1 == 3
        page2, total2 = await store.list_sessions(
            participant_id="p_1", limit=2, offset=2
        )
        assert [r.id for r in page2] == ["s_1"]
        assert total2 == 3
        beyond, total3 = await store.list_sessions(
            participant_id="p_1", limit=2, offset=5
        )
        assert beyond == []
        assert total3 == 3


# -- resolve_access (issue #151) --------------------------------------------------


async def _seed_agent_participant(
    session: AsyncSession, *, agent_id: str, participant_id: str, label: str
) -> None:
    session.add(Agent(id=agent_id, name=label))
    session.add(Participant(id=participant_id, agent_id=agent_id, label=label))
    await session.flush()


async def test_resolve_access_owner_granted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        session.add(SessionParticipant(session_id="s_1", participant_id="p_1"))
        await session.commit()

    async with session_factory() as session:
        verdict = await SessionStore(session).resolve_access("s_1", "u_1")
        assert verdict == "granted"


async def test_resolve_access_live_member_granted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A non-owner with a current (left_at NULL) membership is granted."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _seed_user(
            session, user_id="u_2", participant_id="p_2", display_name="Bob"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        session.add(SessionParticipant(session_id="s_1", participant_id="p_2"))
        await session.commit()

    async with session_factory() as session:
        verdict = await SessionStore(session).resolve_access("s_1", "u_2")
        assert verdict == "granted"


async def test_resolve_access_left_member_denied(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _seed_user(
            session, user_id="u_2", participant_id="p_2", display_name="Bob"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        session.add(
            SessionParticipant(
                session_id="s_1", participant_id="p_2", left_at=_dt(3)
            )
        )
        await session.commit()

    async with session_factory() as session:
        verdict = await SessionStore(session).resolve_access("s_1", "u_2")
        assert verdict == "denied"


async def test_resolve_access_stranger_denied(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _seed_user(
            session, user_id="u_2", participant_id="p_2", display_name="Bob"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        await session.commit()

    async with session_factory() as session:
        verdict = await SessionStore(session).resolve_access("s_1", "u_2")
        assert verdict == "denied"


async def test_resolve_access_missing_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        verdict = await SessionStore(session).resolve_access(
            "s_ghost", "u_1"
        )
        assert verdict == "missing"


async def test_resolve_access_owner_without_membership_still_granted(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The owner axis is independent of membership rows (the #121 create path
    always writes one, but the guard must not depend on it)."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        await session.commit()  # no SessionParticipant row

    async with session_factory() as session:
        verdict = await SessionStore(session).resolve_access("s_1", "u_1")
        assert verdict == "granted"


# -- list_message_page (issue #151) ------------------------------------------------


async def test_message_page_filters_kinds_and_orders_by_seq(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Only user/assistant messages; seq ascending; other kinds excluded."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        await _append(
            session, "s_1", EventKind.USER_MESSAGE, "hi", author="p_1"
        )
        await _append(session, "s_1", EventKind.TOOL_CALL, "tool")
        await _append(
            session, "s_1", EventKind.ASSISTANT_MESSAGE, "hello", author="p_1"
        )
        await _append(session, "s_1", EventKind.SYSTEM, "notice")
        await _append(session, "s_1", EventKind.TOOL_RESULT, "out")
        await session.commit()

    async with session_factory() as session:
        rows = await SessionStore(session).list_message_page(
            "s_1", after_seq=0, limit=10
        )
        assert [(r.seq, r.kind) for r in rows] == [
            (1, "user_message"),
            (3, "assistant_message"),
        ]


async def test_message_page_after_seq_excludes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        for i in range(1, 6):
            await _append(
                session,
                "s_1",
                EventKind.USER_MESSAGE,
                f"m{i}",
                author="p_1",
            )
        await session.commit()

    async with session_factory() as session:
        rows = await SessionStore(session).list_message_page(
            "s_1", after_seq=2, limit=10
        )
        assert [r.seq for r in rows] == [3, 4, 5]


async def test_message_page_limit_respected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        for i in range(5):
            await _append(
                session, "s_1", EventKind.USER_MESSAGE, f"m{i}", author="p_1"
            )
        await session.commit()

    async with session_factory() as session:
        rows = await SessionStore(session).list_message_page(
            "s_1", after_seq=0, limit=2
        )
        assert [r.seq for r in rows] == [1, 2]


async def test_message_page_joins_author_labels(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """User and agent participants both render their label."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _seed_agent_participant(
            session, agent_id="a_1", participant_id="p_agent", label="Octave"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        await _append(
            session, "s_1", EventKind.USER_MESSAGE, "hi", author="p_1"
        )
        await _append(
            session,
            "s_1",
            EventKind.ASSISTANT_MESSAGE,
            "hello",
            author="p_agent",
        )
        await session.commit()

    async with session_factory() as session:
        rows = await SessionStore(session).list_message_page(
            "s_1", after_seq=0, limit=10
        )
        assert [r.author_label for r in rows] == ["Alice", "Octave"]
        assert [r.content for r in rows] == ["hi", "hello"]


async def test_message_page_null_author_label_none(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """NULL author (schema-nullable FK) renders label=None, not a crash."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        await _append(session, "s_1", EventKind.USER_MESSAGE, "orphan")
        await session.commit()

    async with session_factory() as session:
        rows = await SessionStore(session).list_message_page(
            "s_1", after_seq=0, limit=10
        )
        assert rows[0].author_label is None


async def test_message_page_never_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Store contract: the page is a read; nothing is committed."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        await _append(
            session, "s_1", EventKind.USER_MESSAGE, "hi", author="p_1"
        )
        await session.commit()
    async with session_factory() as session:
        rows = await SessionStore(session).list_message_page(
            "s_1", after_seq=0, limit=10
        )
        assert len(rows) == 1


# -- list_message_page_before (issue #43) -------------------------------------------


async def _seed_five_messages(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """s_1 owned by u_1 with user/assistant messages at seq 1..5."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        for i in range(1, 6):
            kind = (
                EventKind.USER_MESSAGE
                if i % 2
                else EventKind.ASSISTANT_MESSAGE
            )
            await _append(session, "s_1", kind, f"m{i}", author="p_1")
        await session.commit()


async def test_message_page_before_none_returns_newest_desc(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Unbounded upper edge: the newest `limit` rows, seq DESC (chat-open)."""
    await _seed_five_messages(session_factory)
    async with session_factory() as session:
        rows = await SessionStore(session).list_message_page_before(
            "s_1", before_seq=None, limit=2
        )
        assert [r.seq for r in rows] == [5, 4]
        assert [r.content for r in rows] == ["m5", "m4"]


async def test_message_page_before_seq_excludes_upper_window(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """seq >= before_seq never appears; older rows come back DESC."""
    await _seed_five_messages(session_factory)
    async with session_factory() as session:
        rows = await SessionStore(session).list_message_page_before(
            "s_1", before_seq=4, limit=10
        )
        assert [r.seq for r in rows] == [3, 2, 1]


async def test_message_page_before_filters_kinds(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Non-message kinds are excluded, same filter as the forward page."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        await _append(session, "s_1", EventKind.USER_MESSAGE, "hi", author="p_1")
        await _append(session, "s_1", EventKind.SYSTEM, "notice")
        await _append(
            session, "s_1", EventKind.ASSISTANT_MESSAGE, "hello", author="p_1"
        )
        await session.commit()

    async with session_factory() as session:
        rows = await SessionStore(session).list_message_page_before(
            "s_1", before_seq=None, limit=10
        )
        assert [(r.seq, r.kind) for r in rows] == [
            (3, "assistant_message"),
            (1, "user_message"),
        ]


async def test_message_page_before_walk_terminates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Backward cursor walk: pages cover the transcript without overlap and
    the final page under-fills."""
    await _seed_five_messages(session_factory)
    async with session_factory() as session:
        store = SessionStore(session)
        page1 = await store.list_message_page_before("s_1", before_seq=None, limit=2)
        assert [r.seq for r in page1] == [5, 4]
        page2 = await store.list_message_page_before(
            "s_1", before_seq=page1[-1].seq, limit=2
        )
        assert [r.seq for r in page2] == [3, 2]
        page3 = await store.list_message_page_before(
            "s_1", before_seq=page2[-1].seq, limit=2
        )
        assert [r.seq for r in page3] == [1]
        page4 = await store.list_message_page_before(
            "s_1", before_seq=page3[-1].seq, limit=2
        )
        assert page4 == []


# -- thinking on transcript rows (issue #45) ------------------------------------


async def test_transcript_row_carries_thinking(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        await EventStore(session).append(
            "s_1",
            EventKind.ASSISTANT_MESSAGE,
            author_participant_id="p_1",
            payload={"content": "answer", "thinking": "because"},
        )
        await session.commit()

    async with session_factory() as session:
        rows = await SessionStore(session).list_message_page(
            "s_1", after_seq=0, limit=10
        )
        assert rows[0].content == "answer"
        assert rows[0].thinking == "because"


async def test_transcript_row_thinking_null_for_user_message(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        await _append(
            session, "s_1", EventKind.USER_MESSAGE, "hi", author="p_1"
        )
        await session.commit()

    async with session_factory() as session:
        rows = await SessionStore(session).list_message_page(
            "s_1", after_seq=0, limit=10
        )
        assert rows[0].thinking is None


# -- get_session_owner / delete_session (issue #160) ---------------------------


async def test_get_session_owner_hit_and_miss(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        store = SessionStore(session)
        assert await store.get_session_owner("s_1") == "u_1"
        assert await store.get_session_owner("s_ghost") is None
