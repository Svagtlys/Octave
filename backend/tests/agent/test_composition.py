"""build_message_router wires the full driver (issue #60).

End-to-end over real SQLite with the scripted adapter: one deliver()
produces the whole stage chain in the transcript, in order. The LLM
decider's 1:1 fast path needs no completion call for the single agent.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.agent import AgentInstanceManager
from octave.agent.composition import build_message_router
from octave.agent.router import StopReason
from octave.db.event_store import EventStore
from octave.db.models import Agent, Session, User
from tests.agent.fakes import ScriptedAdapter, final_result, tool_call, tool_result
from tests.agent.test_turn_runner import FakeRegistry  # reuse the fake

_FAKE_BINDING = {"kind": "explicit", "adapter": "fake", "model": "mock-model"}


async def _seed(session_factory) -> None:
    async with session_factory() as session:
        session.add(User(id="u_1", username="alice", display_name="Alice"))
        session.add(Agent(id="a_1", name="Octave", model_binding=_FAKE_BINDING))
        session.add(Session(id="s_1", created_by_user_id="u_1", status="active"))
        await session.commit()
    async with session_factory() as session:
        await AgentInstanceManager(session).spawn(agent_id="a_1", session_id="s_1")
        await session.commit()
    async with session_factory() as session:
        # alice's participant is created by the users' create path in tests:
        # add one manually and make her a member.
        from sqlalchemy import select

        from octave.db.models import Participant, SessionParticipant

        pid = await session.scalar(
            select(Participant.id).where(Participant.user_id == "u_1")
        )
        if pid is None:
            participant = Participant(id="p_u1", user_id="u_1", label="Alice")
            session.add(participant)
            await session.flush()
            pid = participant.id
        session.add(SessionParticipant(session_id="s_1", participant_id=pid))
        await session.commit()


async def test_deliver_produces_full_stage_chain(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__write", "{}")), final_result("Done")]
    )
    async with session_factory() as session:
        router = build_message_router(
            session, adapter=adapter, tool_registry=FakeRegistry(), adapter_name="fake"
        )
        outcome = await router.deliver(
            "s_1", author_participant_id="p_u1", content="make a file"
        )
        await session.commit()
    assert outcome.stop_reason == StopReason.AWAIT_USER
    assert len(outcome.turns) == 1

    async with session_factory() as session:
        events = await EventStore(session).read("s_1")
    assert [e.kind for e in events] == [
        "user_message",
        "context_injection",
        "tool_call",
        "tool_result",
        "assistant_message",
    ]
    # The bracket covers the runner's appends through the reply event.
    turn = outcome.turns[0]
    assert turn.seq_start == 2 and turn.seq_end == 5
