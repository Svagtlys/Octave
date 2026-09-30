"""Bracket reconstruction (design spec Decision 3).

Pure tests build Event objects in memory (never flushed — reconstruct
reads only .seq/.kind/.author_participant_id). The router-equivalence
test (Task 5) proves the rule against the shipped MessageRouter.
"""

from octave.context.brackets import TurnBracket, reconstruct_turns
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
