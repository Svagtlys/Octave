"""Constraint enforcement on a real temp SQLite DB (no mocks)."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.db.models import (
    Agent,
    Event,
    McpServer,
    Participant,
    Session,
    SessionParticipant,
    User,
    VaultItem,
)


async def test_identity_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(Agent(id="a_1", name="Octave", model_tag="quick"))
        session.add(Participant(id="p_1", user_id="u_1", label="Alice"))
        session.add(Participant(id="p_2", agent_id="a_1", label="Octave"))
        await session.commit()
        loaded = await session.get(User, "u_1")
        assert loaded is not None
        assert loaded.display_name == "Alice"
        assert loaded.created_at.tzinfo is not None


async def test_agent_status_defaults_active(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(Agent(id="a_1", name="Octave"))
        await session.commit()
        agent = await session.get(Agent, "a_1")
        assert agent is not None and agent.status == "active"


async def test_participant_requires_one_identity(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(Participant(id="p_bad", label="neither"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_participant_rejects_both_identities(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(Agent(id="a_1", name="Octave"))
        session.add(
            Participant(id="p_bad", user_id="u_1", agent_id="a_1", label="both")
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_participant_unique_per_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(Participant(id="p_1", user_id="u_1", label="Alice"))
        session.add(Participant(id="p_2", user_id="u_1", label="Alice again"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_participant_fk_to_user_enforced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(Participant(id="p_bad", user_id="ghost", label="nobody"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def _seed_chat(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """One user + one agent, both members of session ``s_1``."""
    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(Agent(id="a_1", name="Octave"))
        session.add(Participant(id="p_user", user_id="u_1", label="Alice"))
        session.add(Participant(id="p_agent", agent_id="a_1", label="Octave"))
        session.add(Session(id="s_1", created_by_user_id="u_1"))
        session.add(SessionParticipant(session_id="s_1", participant_id="p_user"))
        session.add(SessionParticipant(session_id="s_1", participant_id="p_agent"))
        await session.commit()


async def test_transcript_round_trip_ordered_by_seq(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_chat(session_factory)
    async with session_factory() as session:
        session.add(
            Event(
                id="e_1", session_id="s_1", seq=1, kind="user_message",
                author_participant_id="p_user", payload={"content": "hi"},
            )
        )
        session.add(
            Event(
                id="e_2", session_id="s_1", seq=2, kind="assistant_message",
                author_participant_id="p_agent", payload={"content": "hello"},
            )
        )
        await session.commit()
    async with session_factory() as session:
        rows = (
            await session.execute(select(Event).order_by(Event.seq))
        ).scalars().all()
    assert [e.kind for e in rows] == ["user_message", "assistant_message"]
    assert rows[0].payload == {"content": "hi"}


async def test_duplicate_seq_in_one_session_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_chat(session_factory)
    async with session_factory() as session:
        session.add(Event(id="e_1", session_id="s_1", seq=1, kind="system", payload={}))
        session.add(
            Event(id="e_1b", session_id="s_1", seq=1, kind="system", payload={})
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_seq_may_repeat_across_sessions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_chat(session_factory)
    async with session_factory() as session:
        session.add(Session(id="s_2", created_by_user_id="u_1"))
        session.add(Event(id="e_a", session_id="s_1", seq=1, kind="system", payload={}))
        session.add(Event(id="e_b", session_id="s_2", seq=1, kind="system", payload={}))
        await session.commit()


async def test_event_target_must_be_session_member(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Composite FK: an event cannot address a participant outside the session."""
    await _seed_chat(session_factory)
    async with session_factory() as session:
        # a_2 (not a_1): a_1 already has participant p_agent, and
        # uq_participants_agent would raise before the composite FK is tested.
        session.add(Agent(id="a_2", name="Stranger"))
        session.add(Participant(id="p_ghost", agent_id="a_2", label="Ghost"))
        await session.commit()
    async with session_factory() as session:
        session.add(
            Event(
                id="e_bad", session_id="s_1", seq=1, kind="system",
                target_participant_id="p_ghost", payload={},
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_event_null_target_is_broadcast(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_chat(session_factory)
    async with session_factory() as session:
        session.add(
            Event(
                id="e_ok", session_id="s_1", seq=1, kind="user_message",
                author_participant_id="p_user", target_participant_id=None,
                payload={"content": "hi"},
            )
        )
        await session.commit()


async def test_duplicate_membership_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(Participant(id="p_user", user_id="u_1", label="Alice"))
        session.add(Session(id="s_1", created_by_user_id="u_1"))
        session.add(SessionParticipant(session_id="s_1", participant_id="p_user"))
        session.add(SessionParticipant(session_id="s_1", participant_id="p_user"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_session_delete_cascades_events_and_membership(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_chat(session_factory)
    async with session_factory() as session:
        session.add(Event(id="e_1", session_id="s_1", seq=1, kind="system", payload={}))
        await session.commit()
    async with session_factory() as session:
        session_obj = await session.get(Session, "s_1")
        assert session_obj is not None
        await session.delete(session_obj)
        await session.commit()
    async with session_factory() as session:
        events = (
            await session.execute(select(func.count()).select_from(Event))
        ).scalar_one()
        members = (
            await session.execute(
                select(func.count()).select_from(SessionParticipant)
            )
        ).scalar_one()
    assert events == 0
    assert members == 0


async def test_parent_session_lineage_and_cascade(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_chat(session_factory)
    async with session_factory() as session:
        session.add(
            Session(id="s_child", created_by_user_id="u_1", parent_session_id="s_1")
        )
        await session.commit()
    async with session_factory() as session:
        parent = await session.get(Session, "s_1")
        assert parent is not None
        await session.delete(parent)
        await session.commit()
    async with session_factory() as session:
        assert await session.get(Session, "s_child") is None


async def test_session_requires_existing_owner(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(Session(id="s_bad", created_by_user_id="ghost"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def _seed_user(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        await session.commit()


async def test_mcp_stdio_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            McpServer(
                id="m_1", name="fs", transport="stdio",
                command="npx", args=["server-fs"], env={"KEY": "secret"},
            )
        )
        await session.commit()
        server = await session.get(McpServer, "m_1")
        assert server is not None
        assert server.args == ["server-fs"]
        assert server.enabled is True


async def test_mcp_http_round_trip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            McpServer(id="m_1", name="remote", transport="http", url="http://x/mcp")
        )
        await session.commit()


async def test_mcp_stdio_requires_command(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(McpServer(id="m_bad", name="bad", transport="stdio"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_mcp_http_rejects_command(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            McpServer(
                id="m_bad", name="bad", transport="http",
                url="http://x/mcp", command="npx",
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_mcp_name_unique(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(McpServer(id="m_1", name="fs", transport="http", url="http://a"))
        session.add(McpServer(id="m_2", name="fs", transport="http", url="http://b"))
        with pytest.raises(IntegrityError):
            await session.commit()


async def test_vault_item_round_trip_with_embedding(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_user(session_factory)
    async with session_factory() as session:
        session.add(
            VaultItem(
                id="v_1", user_id="u_1", kind="preference",
                name="Terse answers", content="Lead with the result.",
                meta={"tags": ["comms"]},
                embedding=b"\x00\x00\x80?" * 8,
                embedding_model="nomic-embed-text", embedding_dim=8,
            )
        )
        await session.commit()
        item = await session.get(VaultItem, "v_1")
        assert item is not None
        assert item.meta == {"tags": ["comms"]}
        assert item.embedding is not None and len(item.embedding) == 32


def test_vault_metadata_column_name_survives_reserved_attribute() -> None:
    """``metadata`` is reserved on DeclarativeBase; attribute ``meta`` maps to
    SQL column ``metadata``."""
    assert "metadata" in VaultItem.__table__.c
    assert VaultItem.__table__.c["metadata"].name == "metadata"


async def test_vault_item_fk_to_user_enforced(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            VaultItem(id="v_bad", user_id="ghost", kind="skill", name="n", content="c")
        )
        with pytest.raises(IntegrityError):
            await session.commit()
