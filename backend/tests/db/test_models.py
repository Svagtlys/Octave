"""Constraint enforcement on a real temp SQLite DB (no mocks)."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.db.models import Agent, Participant, User


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
