"""AgentInstanceManager lifecycle (design spec 2026-09-27).

One write path, real SQLite, explicit commits — mirrors VaultStore testing.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.agent.errors import (
    AgentNotFoundError,
    AgentPausedError,
    InstanceExistsError,
    InstanceNotFoundError,
    InvalidTransitionError,
    ModelBindingError,
    SessionNotFoundError,
    TerminalSessionError,
    TurnInProgressError,
)
from octave.agent.instances import (
    AgentInstanceManager,
    ResolvedModel,
    resolve_model,
)
from octave.db.models import (
    Agent,
    AgentInstance,
    Participant,
    Session,
    SessionParticipant,
    User,
)
from octave.db.types import ExplicitModelBinding, TagModelBinding

_TAG_BINDING = {"kind": "tag", "tag": "quick"}


async def _seed_defs(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    agent_status: str = "active",
    session_status: str = "active",
    model_binding: dict[str, str] | None = _TAG_BINDING,
) -> None:
    """One user, one agent (a_1), one session (s_1); no membership."""
    async with session_factory() as session:
        session.add(User(id="u_1", username="alice", display_name="Alice"))
        session.add(
            Agent(
                id="a_1",
                name="Octave",
                status=agent_status,
                model_binding=model_binding,
            )
        )
        session.add(Session(id="s_1", created_by_user_id="u_1", status=session_status))
        await session.commit()


async def test_spawn_creates_idle_instance_and_membership(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        manager = AgentInstanceManager(session)
        instance = await manager.spawn(agent_id="a_1", session_id="s_1")
        await session.commit()
        assert instance.status == "idle"
        assert instance.agent_id == "a_1" and instance.session_id == "s_1"
    async with session_factory() as session:
        members = (await session.execute(select(SessionParticipant))).scalars().all()
        assert len(members) == 1
        participant = await session.get(Participant, members[0].participant_id)
        assert participant is not None and participant.agent_id == "a_1"
        assert participant.label == "Octave"


async def test_spawn_reuses_existing_participant(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        session.add(Participant(id="p_agent", agent_id="a_1", label="Octave"))
        await session.commit()
    async with session_factory() as session:
        manager = AgentInstanceManager(session)
        await manager.spawn(agent_id="a_1", session_id="s_1")
        await session.commit()
    async with session_factory() as session:
        participants = (await session.execute(select(Participant))).scalars().all()
        assert len(participants) == 1


async def test_spawn_paused_agent_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory, agent_status="paused")
    async with session_factory() as session:
        with pytest.raises(AgentPausedError):
            await AgentInstanceManager(session).spawn(agent_id="a_1", session_id="s_1")


async def test_spawn_terminal_session_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory, session_status="completed")
    async with session_factory() as session:
        with pytest.raises(TerminalSessionError):
            await AgentInstanceManager(session).spawn(agent_id="a_1", session_id="s_1")


async def test_spawn_missing_agent_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        with pytest.raises(AgentNotFoundError):
            await AgentInstanceManager(session).spawn(
                agent_id="ghost", session_id="s_1"
            )


async def test_spawn_missing_session_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        with pytest.raises(SessionNotFoundError):
            await AgentInstanceManager(session).spawn(
                agent_id="a_1", session_id="ghost"
            )


async def test_spawn_missing_binding_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory, model_binding=None)
    async with session_factory() as session:
        with pytest.raises(ModelBindingError):
            await AgentInstanceManager(session).spawn(agent_id="a_1", session_id="s_1")


async def test_spawn_malformed_binding_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory, model_binding={"kind": "magic"})
    async with session_factory() as session:
        with pytest.raises(ModelBindingError):
            await AgentInstanceManager(session).spawn(agent_id="a_1", session_id="s_1")


async def test_spawn_duplicate_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_defs(session_factory)
    async with session_factory() as session:
        manager = AgentInstanceManager(session)
        await manager.spawn(agent_id="a_1", session_id="s_1")
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(InstanceExistsError):
            await AgentInstanceManager(session).spawn(agent_id="a_1", session_id="s_1")
        count = (
            await session.execute(select(AgentInstance))
        ).scalars().all()
        assert len(count) == 1


async def _spawn_idle(
    session_factory: async_sessionmaker[AsyncSession],
) -> str:
    """Seed defs, spawn, commit; return the instance id."""
    await _seed_defs(session_factory)
    async with session_factory() as session:
        instance = await AgentInstanceManager(session).spawn(
            agent_id="a_1", session_id="s_1"
        )
        await session.commit()
        return instance.id


async def test_begin_turn_activates(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        instance = await AgentInstanceManager(session).begin_turn(instance_id)
        await session.commit()
        assert instance.status == "active"


async def test_begin_turn_twice_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        await AgentInstanceManager(session).begin_turn(instance_id)
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(TurnInProgressError):
            await AgentInstanceManager(session).begin_turn(instance_id)


async def test_begin_turn_paused_agent_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        agent = await session.get(Agent, "a_1")
        assert agent is not None
        agent.status = "paused"
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(AgentPausedError):
            await AgentInstanceManager(session).begin_turn(instance_id)


async def test_begin_turn_missing_instance_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        with pytest.raises(InstanceNotFoundError):
            await AgentInstanceManager(session).begin_turn("ghost")


async def test_end_turn_idles(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        manager = AgentInstanceManager(session)
        await manager.begin_turn(instance_id)
        instance = await manager.end_turn(instance_id)
        await session.commit()
        assert instance.status == "idle"


async def test_end_turn_on_idle_raises(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        with pytest.raises(InvalidTransitionError):
            await AgentInstanceManager(session).end_turn(instance_id)


async def test_destroy_deletes_row(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        destroyed = await AgentInstanceManager(session).destroy(instance_id)
        await session.commit()
        assert destroyed is True
    async with session_factory() as session:
        assert await session.get(AgentInstance, instance_id) is None


async def test_destroy_absent_returns_false(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        assert await AgentInstanceManager(session).destroy("ghost") is False


async def test_destroy_active_is_plain_delete(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """No archival hook, no state precondition — destroy is a delete."""
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        manager = AgentInstanceManager(session)
        await manager.begin_turn(instance_id)
        assert await manager.destroy(instance_id) is True
        await session.commit()


async def test_reconcile_resets_stale_active(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _spawn_idle(session_factory)
    async with session_factory() as session:
        await AgentInstanceManager(session).begin_turn(instance_id)
        await session.commit()
    async with session_factory() as session:
        reset = await AgentInstanceManager(session).reconcile()
        await session.commit()
        assert reset == 1
    async with session_factory() as session:
        instance = await session.get(AgentInstance, instance_id)
        assert instance is not None and instance.status == "idle"


async def test_reconcile_noop_when_all_idle(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _spawn_idle(session_factory)
    async with session_factory() as session:
        assert await AgentInstanceManager(session).reconcile() == 0


def test_resolve_explicit_binding() -> None:
    resolved = resolve_model(
        ExplicitModelBinding(kind="explicit", adapter="openai", model="llama3")
    )
    assert resolved == ResolvedModel(adapter="openai", model="llama3")


def test_resolve_tag_binding_with_lookup() -> None:
    resolved = resolve_model(
        TagModelBinding(kind="tag", tag="quick"), tag_lookup=lambda tag: "llama3"
    )
    assert resolved == ResolvedModel(adapter=None, model="llama3")


def test_resolve_tag_miss_raises() -> None:
    with pytest.raises(ModelBindingError):
        resolve_model(
            TagModelBinding(kind="tag", tag="nope"), tag_lookup=lambda t: None
        )


def test_resolve_tag_without_lookup_raises() -> None:
    with pytest.raises(ModelBindingError):
        resolve_model(TagModelBinding(kind="tag", tag="quick"))
