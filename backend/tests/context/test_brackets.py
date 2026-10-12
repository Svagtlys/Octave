"""Bracket reconstruction (design spec Decision 3).

Pure tests build Event objects in memory (never flushed — reconstruct
reads only .seq/.kind/.author_participant_id). The router-equivalence
test (Task 5) proves the rule against the shipped MessageRouter.
"""

from octave.context.brackets import TurnBracket, reconstruct_turns
from octave.db.event_store import EventStore
from octave.db.models import Event
from octave.db.types import EventKind

MAP = {"p_a1": "a_1", "p_a2": "a_2"}


def _event(seq: int, kind: EventKind, author: str | None = None) -> Event:
    return Event(
        id=f"e{seq}",
        session_id="s_1",
        seq=seq,
        kind=str(kind),
        author_participant_id=author,
        payload={},
    )


def test_single_turn_bracket() -> None:
    events = [
        _event(1, EventKind.USER_MESSAGE, "p_u1"),
        _event(2, EventKind.TOOL_CALL, "p_a1"),
        _event(3, EventKind.TOOL_RESULT, "p_a1"),
        _event(4, EventKind.ASSISTANT_MESSAGE, "p_a1"),
    ]
    assert reconstruct_turns(events, MAP) == [TurnBracket("a_1", 2, 4)]


def test_contiguous_brackets_same_agent() -> None:
    events = [
        _event(1, EventKind.USER_MESSAGE, "p_u1"),
        _event(2, EventKind.TOOL_CALL, "p_a1"),
        _event(3, EventKind.ASSISTANT_MESSAGE, "p_a1"),
        _event(4, EventKind.TOOL_CALL, "p_a1"),
        _event(5, EventKind.ASSISTANT_MESSAGE, "p_a1"),
    ]
    assert reconstruct_turns(events, MAP) == [
        TurnBracket("a_1", 2, 3),
        TurnBracket("a_1", 4, 5),
    ]


def test_multi_agent_alternation() -> None:
    events = [
        _event(1, EventKind.USER_MESSAGE, "p_u1"),
        _event(2, EventKind.TOOL_CALL, "p_a1"),
        _event(3, EventKind.ASSISTANT_MESSAGE, "p_a1"),
        _event(4, EventKind.TOOL_CALL, "p_a2"),
        _event(5, EventKind.ASSISTANT_MESSAGE, "p_a2"),
    ]
    assert reconstruct_turns(events, MAP) == [
        TurnBracket("a_1", 2, 3),
        TurnBracket("a_2", 4, 5),
    ]


def test_failed_turn_orphans_produce_no_bracket() -> None:
    events = [
        _event(1, EventKind.USER_MESSAGE, "p_u1"),
        _event(2, EventKind.TOOL_CALL, "p_a1"),
        _event(3, EventKind.TOOL_RESULT, "p_a1"),
        _event(4, EventKind.SYSTEM, None),  # failure notice, unauthored
    ]
    assert reconstruct_turns(events, MAP) == []


def test_trailing_unclosed_events_produce_no_bracket() -> None:
    events = [
        _event(1, EventKind.USER_MESSAGE, "p_u1"),
        _event(2, EventKind.TOOL_CALL, "p_a1"),
    ]
    assert reconstruct_turns(events, MAP) == []


def test_anchor_mid_stream_opens_next_region_after_it() -> None:
    events = [
        _event(1, EventKind.USER_MESSAGE, "p_u1"),
        _event(2, EventKind.ASSISTANT_MESSAGE, "p_a1"),
        _event(3, EventKind.USER_MESSAGE, "p_u1"),  # anchor between turns
        _event(4, EventKind.TOOL_CALL, "p_a1"),
        _event(5, EventKind.ASSISTANT_MESSAGE, "p_a1"),
    ]
    assert reconstruct_turns(events, MAP) == [
        TurnBracket("a_1", 2, 2),
        TurnBracket("a_1", 4, 5),
    ]


def test_empty_transcript() -> None:
    assert reconstruct_turns([], MAP) == []


async def test_matches_router_turn_records(session_factory) -> None:
    """The reconstruction rule agrees with the shipped router's claim
    points on a committed transcript (design spec Decision 3's pin)."""
    from sqlalchemy import select

    from octave.agent import AgentInstanceManager, AgentRegistry
    from octave.agent.decider import Decision, DecisionState
    from octave.agent.router import MessageRouter
    from octave.db.models import (
        Agent,
        Participant,
        Session,
        SessionParticipant,
        User,
    )

    async with session_factory() as session:
        session.add(User(id="u_1", username="alice", display_name="Alice"))
        session.add(
            Agent(id="a_1", name="Echo", model_binding={"kind": "tag", "tag": "quick"})
        )
        session.add(
            Agent(
                id="a_2", name="Second", model_binding={"kind": "tag", "tag": "quick"}
            )
        )
        session.add(Session(id="s_1", created_by_user_id="u_1", status="active"))
        await session.commit()

    async def _spawn(agent_id: str) -> None:
        async with session_factory() as session:
            await AgentInstanceManager(session).spawn(
                agent_id=agent_id, session_id="s_1"
            )
            await session.commit()

    async def _participant_of(agent_id: str) -> str:
        async with session_factory() as session:
            row = await session.execute(
                select(Participant.id).where(Participant.agent_id == agent_id)
            )
            return row.scalar_one()

    async with session_factory() as session:
        session.add(Participant(id="p_u1", user_id="u_1", label="Alice"))
        await session.flush()
        session.add(SessionParticipant(session_id="s_1", participant_id="p_u1"))
        await session.commit()

    await _spawn("a_1")
    await _spawn("a_2")
    p_a1 = await _participant_of("a_1")
    p_a2 = await _participant_of("a_2")

    class ScriptedDecider:
        def __init__(self, choices: list[str]) -> None:
            self._choices = list(choices)

        async def decide(self, state: DecisionState) -> str:
            return self._choices.pop(0)

    class RecordingRunner:
        def __init__(self, replies: list[str]) -> None:
            self._replies = list(replies)

        async def run_turn(self, *, instance, messages) -> str:  # type: ignore[no-untyped-def]
            return self._replies.pop(0)

    async with session_factory() as session:
        router = MessageRouter(
            session=session,
            manager=AgentInstanceManager(session),
            registry=AgentRegistry(session),
            decider=ScriptedDecider([p_a1, p_a2, Decision.AWAIT_USER]),
            turn_runner=RecordingRunner(["first", "second"]),
        )
        outcome = await router.deliver(
            "s_1", author_participant_id="p_u1", content="go"
        )
        await session.commit()

    async with session_factory() as session:
        events = await EventStore(session).read("s_1")
        rows = await session.execute(
            select(Participant.id, Participant.agent_id).where(
                Participant.agent_id.is_not(None)
            )
        )
        agent_by_participant = {pid: aid for pid, aid in rows.all()}

    brackets = reconstruct_turns(events, agent_by_participant)
    assert [(b.agent_id, b.seq_start, b.seq_end) for b in brackets] == [
        (t.agent_id, t.seq_start, t.seq_end) for t in outcome.turns
    ]
