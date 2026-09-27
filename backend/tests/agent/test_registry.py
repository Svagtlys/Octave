"""AgentRegistry read surface (design spec 2026-09-27-agent-registry).

Read-only queries over the #25 substrate: real SQLite via session_factory,
spawns driven through AgentInstanceManager (the only write path).
"""

import logging

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.agent import AgentInstanceManager, InstanceNotFoundError
from octave.agent.registry import AgentRegistry
from octave.db.models import Agent, AgentInstance, Session, User
from octave.db.types import InstanceStatus

_TAG_BINDING = {"kind": "tag", "tag": "quick"}
_ASSIGNMENTS = {
    "prompt": "v_p",
    "skills": ["v_s1", "v_s2"],
    "preference_tags": ["terse"],
}


async def _seed_defs(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """One user; agents a_1/a_2 active + a_3 paused; sessions s_1/s_2."""
    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(
            Agent(
                id="a_1",
                name="Octave",
                model_binding=_TAG_BINDING,
                assignments=dict(_ASSIGNMENTS),
            )
        )
        session.add(Agent(id="a_2", name="Helper", model_binding=_TAG_BINDING))
        session.add(
            Agent(
                id="a_3", name="Paused", status="paused", model_binding=_TAG_BINDING
            )
        )
        session.add(
            Session(id="s_1", created_by_user_id="u_1", status="active")
        )
        session.add(
            Session(id="s_2", created_by_user_id="u_1", status="active")
        )
        await session.commit()


async def _spawn(
    session_factory: async_sessionmaker[AsyncSession],
    agent_id: str,
    session_id: str,
) -> str:
    async with session_factory() as session:
        instance = await AgentInstanceManager(session).spawn(
            agent_id=agent_id, session_id=session_id
        )
        await session.commit()
        return instance.id


async def test_list_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        assert await AgentRegistry(session).list_instances() == []


async def test_list_returns_joined_fields(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    instance_id = await _spawn(session_factory, "a_1", "s_1")
    async with session_factory() as session:
        [running] = await AgentRegistry(session).list_instances()
    assert running.instance_id == instance_id
    assert running.agent_id == "a_1"
    assert running.agent_name == "Octave"
    assert running.definition_status == "active"
    assert running.instance_status == "idle"
    assert running.session_id == "s_1"
    assert running.assignments.prompt == "v_p"
    assert running.assignments.skills == ["v_s1", "v_s2"]
    assert running.assignments.preference_tags == ["terse"]
    assert running.created_at <= running.updated_at


async def test_list_filters_compose(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    i1 = await _spawn(session_factory, "a_1", "s_1")
    i2 = await _spawn(session_factory, "a_2", "s_1")
    i3 = await _spawn(session_factory, "a_1", "s_2")
    async with session_factory() as session:
        registry = AgentRegistry(session)
        by_agent = await registry.list_instances(agent_id="a_1")
        by_session = await registry.list_instances(session_id="s_1")
        by_status = await registry.list_instances(status=InstanceStatus.IDLE)
        combined = await registry.list_instances(agent_id="a_1", session_id="s_1")
    assert {r.instance_id for r in by_agent} == {i1, i3}
    assert {r.instance_id for r in by_session} == {i1, i2}
    assert {r.instance_id for r in by_status} == {i1, i2, i3}
    assert [r.instance_id for r in combined] == [i1]


async def test_list_ordering_deterministic(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    ids = [await _spawn(session_factory, a, s) for a, s in
           [("a_1", "s_1"), ("a_2", "s_1"), ("a_1", "s_2")]]
    async with session_factory() as session:
        rows = await AgentRegistry(session).list_instances()
    keys = [(r.created_at, r.instance_id) for r in rows]
    assert keys == sorted(keys)
    assert {r.instance_id for r in rows} == set(ids)


async def test_get_instance_returns_joined_record(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    instance_id = await _spawn(session_factory, "a_1", "s_1")
    async with session_factory() as session:
        running = await AgentRegistry(session).get_instance(instance_id)
    assert running.instance_id == instance_id
    assert running.agent_name == "Octave"
    assert running.assignments.prompt == "v_p"


async def test_get_instance_missing_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        with pytest.raises(InstanceNotFoundError):
            await AgentRegistry(session).get_instance("nope")


async def test_count_by_status_zero_filled(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        counts = await AgentRegistry(session).count_by_status()
    assert counts == {InstanceStatus.IDLE: 0, InstanceStatus.ACTIVE: 0}


async def test_count_by_status_mixed(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    await _spawn(session_factory, "a_1", "s_1")
    await _spawn(session_factory, "a_2", "s_1")
    i3 = await _spawn(session_factory, "a_1", "s_2")
    async with session_factory() as session:
        await AgentInstanceManager(session).begin_turn(i3)
        await session.commit()
    async with session_factory() as session:
        counts = await AgentRegistry(session).count_by_status()
    assert counts == {InstanceStatus.IDLE: 2, InstanceStatus.ACTIVE: 1}


async def test_corrupt_assignments_warn_and_empty(
    session_factory: async_sessionmaker[AsyncSession], caplog
) -> None:
    await _seed_defs(session_factory)
    await _spawn(session_factory, "a_1", "s_1")
    async with session_factory() as session:
        await session.execute(
            update(Agent).where(Agent.id == "a_1").values(assignments={"skills": "not-a-list"})
        )
        await session.commit()
    with caplog.at_level(logging.WARNING, logger="octave.agent.registry"):
        async with session_factory() as session:
            [running] = await AgentRegistry(session).list_instances()
    assert running.assignments.prompt is None
    assert running.assignments.skills == []
    assert running.assignments.preference_tags == []
    assert "invalid assignments" in caplog.text


async def test_bogus_instance_status_fails_loud(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    instance_id = await _spawn(session_factory, "a_1", "s_1")
    async with session_factory() as session:
        await session.execute(
            update(AgentInstance)
            .where(AgentInstance.id == instance_id)
            .values(status="bogus")
        )
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(ValueError):
            await AgentRegistry(session).list_instances()
