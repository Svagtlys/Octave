"""MessageRouter driver (design spec 2026-09-28).

Real SQLite via session_factory; spawns driven through AgentInstanceManager;
scripted decider/runner fakes (no LLM). Transcript assertions read events
back through EventStore on a fresh session.
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.agent import AgentInstanceManager, AgentRegistry
from octave.agent.decider import Decision, DecisionState
from octave.agent.errors import DeciderChoiceError, NotAMemberError
from octave.agent.router import MessageRouter, StopReason
from octave.db.event_store import EventStore
from octave.db.models import Agent, Participant, Session, SessionParticipant, User
from octave.db.types import EventKind, InstanceStatus
from octave.inference.errors import AdapterConnectionError
from octave.inference.types import Message

_TAG_BINDING = {"kind": "tag", "tag": "quick"}


async def _seed(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(Agent(id="a_1", name="Echo", model_binding=_TAG_BINDING))
        session.add(Agent(id="a_2", name="Second", model_binding=_TAG_BINDING))
        session.add(Session(id="s_1", created_by_user_id="u_1", status="active"))
        await session.commit()


async def _spawn(
    session_factory: async_sessionmaker[AsyncSession], agent_id: str
) -> str:
    async with session_factory() as session:
        instance = await AgentInstanceManager(session).spawn(
            agent_id=agent_id, session_id="s_1"
        )
        await session.commit()
        return instance.id


async def _invite_user(session_factory: async_sessionmaker[AsyncSession]) -> str:
    """User participant + live membership in s_1."""
    async with session_factory() as session:
        participant = Participant(id="p_u1", user_id="u_1", label="Alice")
        session.add(participant)
        await session.flush()
        session.add(
            SessionParticipant(session_id="s_1", participant_id="p_u1")
        )
        await session.commit()
    return "p_u1"


async def _participant_of(
    session_factory: async_sessionmaker[AsyncSession], agent_id: str
) -> str:
    async with session_factory() as session:
        row = await session.execute(
            select(Participant.id).where(Participant.agent_id == agent_id)
        )
        return row.scalar_one()


class ScriptedDecider:
    """Pops queued choices (Exception items raise); records each state."""

    def __init__(self, choices: list[str]) -> None:
        self._choices = list(choices)
        self.states: list[DecisionState] = []

    async def decide(self, state: DecisionState) -> str:
        self.states.append(state)
        item = self._choices.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class RecordingRunner:
    """Returns queued replies (Exception items raise); records inputs."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[str, list[Message]]] = []

    async def run_turn(self, *, instance, messages: list[Message]) -> str:  # type: ignore[no-untyped-def]
        self.calls.append((instance.instance_id, list(messages)))
        item = self._replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _router(
    session: AsyncSession,
    *,
    decider: ScriptedDecider,
    runner: RecordingRunner,
    max_agent_turns: int = 4,
) -> MessageRouter:
    return MessageRouter(
        session=session,
        manager=AgentInstanceManager(session),
        registry=AgentRegistry(session),
        decider=decider,
        turn_runner=runner,
        max_agent_turns=max_agent_turns,
    )


async def _deliver(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    decider: ScriptedDecider,
    runner: RecordingRunner,
    author: str = "p_u1",
    max_agent_turns: int = 4,
):
    async with session_factory() as session:
        router = _router(
            session, decider=decider, runner=runner, max_agent_turns=max_agent_turns
        )
        outcome = await router.deliver(
            "s_1", author_participant_id=author, content="Hello"
        )
        await session.commit()
        return outcome


async def _transcript(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[int, str, str | None, str]]:
    async with session_factory() as session:
        events = await EventStore(session).read("s_1")
    return [
        (e.seq, e.kind, e.author_participant_id, str(e.payload.get("content", "")))
        for e in events
    ]


async def test_one_to_one_end_to_end(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    p_ai1 = await _participant_of(session_factory, "a_1")
    await _invite_user(session_factory)
    decider = ScriptedDecider([p_ai1, Decision.AWAIT_USER])
    runner = RecordingRunner(["Hi there"])
    outcome = await _deliver(session_factory, decider=decider, runner=runner)
    assert outcome.stop_reason == StopReason.AWAIT_USER
    assert len(outcome.turns) == 1
    assert outcome.turns[0].agent_id == "a_1"
    assert outcome.turns[0].seq_start == outcome.turns[0].seq_end == 2
    assert await _transcript(session_factory) == [
        (1, "user_message", "p_u1", "Hello"),
        (2, "assistant_message", p_ai1, "Hi there"),
    ]


async def test_multi_agent_handoff(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    await _spawn(session_factory, "a_2")
    p_ai1 = await _participant_of(session_factory, "a_1")
    p_ai2 = await _participant_of(session_factory, "a_2")
    await _invite_user(session_factory)
    decider = ScriptedDecider([p_ai1, p_ai2, Decision.AWAIT_USER])
    runner = RecordingRunner(["from AI1", "from AI2"])
    outcome = await _deliver(session_factory, decider=decider, runner=runner)
    assert [t.agent_id for t in outcome.turns] == ["a_1", "a_2"]
    # AI2's runner input contains U1's message AND AI1's reply
    assert runner.calls[1][1] == [
        Message(role="user", content="Hello"),
        Message(role="assistant", content="from AI1"),
    ]
    kinds = [(seq, kind) for seq, kind, _, _ in await _transcript(session_factory)]
    assert kinds == [
        (1, "user_message"),
        (2, "assistant_message"),
        (3, "assistant_message"),
    ]


async def test_empty_roster_awaits_user_immediately(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _invite_user(session_factory)
    decider = ScriptedDecider([])  # never consulted
    runner = RecordingRunner([])
    outcome = await _deliver(session_factory, decider=decider, runner=runner)
    assert outcome.stop_reason == StopReason.AWAIT_USER
    assert outcome.turns == []
    assert runner.calls == []
    assert await _transcript(session_factory) == [(1, "user_message", "p_u1", "Hello")]


async def test_runner_failure_releases_instance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    await _invite_user(session_factory)
    p_ai1 = await _participant_of(session_factory, "a_1")
    decider = ScriptedDecider([p_ai1])
    runner = RecordingRunner([RuntimeError("boom")])
    outcome = await _deliver(session_factory, decider=decider, runner=runner)
    assert outcome.stop_reason == StopReason.ERROR
    assert outcome.error == "boom"
    assert outcome.turns == []  # failed turn contributes no TurnRecord
    transcript = await _transcript(session_factory)
    assert [(seq, kind) for seq, kind, _, _ in transcript] == [
        (1, "user_message"),
        (2, "system"),
    ]
    assert "boom" in transcript[1][3]
    async with session_factory() as session:
        counts = await AgentRegistry(session).count_by_status()
    assert counts[InstanceStatus.ACTIVE] == 0  # turn released


async def test_hop_limit_stops_agent_chatter(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    await _invite_user(session_factory)
    p_ai1 = await _participant_of(session_factory, "a_1")
    decider = ScriptedDecider([p_ai1])  # one decision; loop stops at limit
    runner = RecordingRunner(["first"])
    outcome = await _deliver(
        session_factory, decider=decider, runner=runner, max_agent_turns=1
    )
    assert outcome.stop_reason == StopReason.HOP_LIMIT
    assert len(outcome.turns) == 1


async def test_decider_invalid_falls_back_to_await_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    await _invite_user(session_factory)
    decider = ScriptedDecider(
        [DeciderChoiceError("bad1"), DeciderChoiceError("bad2")]
    )
    runner = RecordingRunner([])
    outcome = await _deliver(session_factory, decider=decider, runner=runner)
    assert outcome.stop_reason == StopReason.AWAIT_USER
    assert outcome.turns == []
    assert runner.calls == []


async def test_decider_adapter_failure_degrades_to_await_user(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Spec: an AdapterError from the decider hits the driver's
    decider-failure path (retry once, then await user) — NOT a rollback that
    loses the user message. Two queued errors also pin the retry count: a
    third consultation would IndexError."""
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    await _invite_user(session_factory)
    decider = ScriptedDecider(
        [AdapterConnectionError("engine down"), AdapterConnectionError("still down")]
    )
    runner = RecordingRunner([])
    outcome = await _deliver(session_factory, decider=decider, runner=runner)
    assert outcome.stop_reason == StopReason.AWAIT_USER
    assert outcome.turns == []
    # User message survives: deliver() returned normally, caller committed
    assert await _transcript(session_factory) == [
        (1, "user_message", "p_u1", "Hello")
    ]


class _ToolAppendingRunner(RecordingRunner):
    """Appends tool events inside the claimed bracket, like the real runner
    (Integration #1: context assembly + ToolLoop) will."""

    def __init__(self, session: AsyncSession, replies: list[str]) -> None:
        super().__init__(replies)
        self._events = EventStore(session)

    async def run_turn(self, *, instance, messages: list[Message]) -> str:  # type: ignore[no-untyped-def]
        await self._events.append(
            "s_1", EventKind.TOOL_CALL, payload={"tool": "search", "args": {}}
        )
        await self._events.append(
            "s_1", EventKind.TOOL_RESULT, payload={"result": "4 hits"}
        )
        return await super().run_turn(instance=instance, messages=messages)


async def test_turn_record_covers_full_bracket(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """TurnRecord's seq range is the whole claimed bracket — events the
    runner appends during the turn included — not just the reply event.
    CM #5's archival keys on this range."""
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    p_ai1 = await _participant_of(session_factory, "a_1")
    await _invite_user(session_factory)
    async with session_factory() as session:
        runner = _ToolAppendingRunner(session, ["here are 4 hits", "second"])
        router = _router(
            session,
            decider=ScriptedDecider([p_ai1, p_ai1, Decision.AWAIT_USER]),
            runner=runner,
        )
        outcome = await router.deliver(
            "s_1", author_participant_id="p_u1", content="Hello"
        )
        await session.commit()
    first, second = outcome.turns
    assert (first.seq_start, first.seq_end) == (2, 4)
    assert (second.seq_start, second.seq_end) == (5, 7)
    kinds = [(seq, kind) for seq, kind, _, _ in await _transcript(session_factory)]
    assert kinds == [
        (1, "user_message"),
        (2, "tool_call"),
        (3, "tool_result"),
        (4, "assistant_message"),
        (5, "tool_call"),
        (6, "tool_result"),
        (7, "assistant_message"),
    ]
    # Turn 2's input picks up turn 1's bracket via the cursor: tool events
    # skipped, reply included.
    assert runner.calls[1][1] == [
        Message(role="user", content="Hello"),
        Message(role="assistant", content="here are 4 hits"),
    ]


async def test_non_member_author_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    async with session_factory() as session:
        router = _router(
            session, decider=ScriptedDecider([]), runner=RecordingRunner([])
        )
        try:
            with pytest.raises(NotAMemberError):
                await router.deliver(
                    "s_1", author_participant_id="p_ghost", content="Hello"
                )
        finally:
            await session.rollback()
    assert await _transcript(session_factory) == []  # nothing appended


async def test_registry_consistent_after_loop(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_factory)
    await _spawn(session_factory, "a_1")
    await _spawn(session_factory, "a_2")
    p_ai1 = await _participant_of(session_factory, "a_1")
    p_ai2 = await _participant_of(session_factory, "a_2")
    await _invite_user(session_factory)
    decider = ScriptedDecider([p_ai1, p_ai2, Decision.AWAIT_USER])
    runner = RecordingRunner(["one", "two"])
    await _deliver(session_factory, decider=decider, runner=runner)
    async with session_factory() as session:
        counts = await AgentRegistry(session).count_by_status()
    assert counts[InstanceStatus.ACTIVE] == 0
    assert counts[InstanceStatus.IDLE] == 2
