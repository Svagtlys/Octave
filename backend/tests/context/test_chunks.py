"""Verbatim chunk rendering + max_chars safety split (Decisions 4/5)."""

from octave.context.brackets import TurnBracket
from octave.context.chunks import chunk_item_id, plan_chunks, render_bracket
from octave.db.models import Event
from octave.db.types import EventKind


def _event(seq: int, kind: EventKind, payload: dict) -> Event:
    return Event(
        id=f"e{seq}",
        session_id="s_1",
        seq=seq,
        kind=str(kind),
        author_participant_id="p_a1",
        payload=payload,
    )


BRACKET = TurnBracket("a_1", 2, 4)


def _bracket_events() -> list[Event]:
    return [
        _event(2, EventKind.TOOL_CALL, {"tool_name": "web.search", "arguments": '{"q": "x"}'}),
        _event(3, EventKind.TOOL_RESULT, {"content": "Sunny 21C"}),
        _event(4, EventKind.ASSISTANT_MESSAGE, {"content": "It is sunny."}),
    ]


def test_render_verbatim_full_content_labeled() -> None:
    text = render_bracket(_bracket_events(), "Echo")
    assert text == (
        'Tool call: web.search {"q": "x"}\n'
        "Tool result: Sunny 21C\n"
        "Echo: It is sunny."
    )


def test_render_tool_result_without_content_uses_json() -> None:
    events = [_event(2, EventKind.TOOL_RESULT, {"structured": {"a": 1}})]
    assert render_bracket(events, "Echo") == 'Tool result: {"a": 1}'


def test_plan_single_chunk_identity_and_meta() -> None:
    plans = plan_chunks(
        session_id="s_1", bracket=BRACKET, events=_bracket_events(),
        label="Echo", max_chars=6000,
    )
    assert len(plans) == 1
    plan = plans[0]
    assert plan.item_id == "transcript_chunk:s_1:2-4"
    assert plan.name == "Echo · events 2–4"
    assert plan.meta == {
        "session_id": "s_1", "agent_id": "a_1", "seq_start": 2, "seq_end": 4,
        "part": 1, "part_total": 1,
    }


def test_plan_split_at_whitespace_boundary() -> None:
    words = "lorem ipsum dolor sit amet " * 10  # 290 chars
    events = [_event(2, EventKind.ASSISTANT_MESSAGE, {"content": words.strip()})]
    plans = plan_chunks(
        session_id="s_1", bracket=TurnBracket("a_1", 2, 2), events=events,
        label="Echo", max_chars=100,
    )
    assert len(plans) > 1
    assert all(len(p.content) <= 100 for p in plans)
    assert all("  " not in p.content.strip() for p in plans)  # no glued words
    assert plans[0].item_id == "transcript_chunk:s_1:2-2:1"
    assert plans[-1].item_id == f"transcript_chunk:s_1:2-2:{len(plans)}"
    assert plans[0].meta["part_total"] == len(plans)
    assert plans[0].name.endswith(f"(part 1/{len(plans)})")
    # parts rejoin to the original (modulo whitespace normalization at cuts)
    assert " ".join(p.content for p in plans) == f"Echo: {words.strip()}"


def test_split_hard_cut_for_single_long_token() -> None:
    # The only whitespace is the label's trailing space, so the whitespace
    # rule cuts "Echo:" off first; the 250-token run then exercises the
    # hard-cut branch (cut == max_chars, guaranteeing progress). Invariants
    # over exact slices: every part fits max_chars, hard-cut parts are
    # exactly max_chars, and the parts rejoin to the original.
    events = [_event(2, EventKind.ASSISTANT_MESSAGE, {"content": "x" * 250})]
    plans = plan_chunks(
        session_id="s_1", bracket=TurnBracket("a_1", 2, 2), events=events,
        label="Echo", max_chars=100,
    )
    lengths = [len(p.content) for p in plans]
    assert all(length <= 100 for length in lengths)
    assert lengths.count(100) == 2  # the hard-cut parts
    assert " ".join(p.content for p in plans).replace(" ", "") == (
        f"Echo:{'x' * 250}"
    )


def test_chunk_item_id_no_part_suffix_when_single() -> None:
    assert chunk_item_id("s_1", BRACKET, 1, 1) == "transcript_chunk:s_1:2-4"
    assert chunk_item_id("s_1", BRACKET, 2, 3) == "transcript_chunk:s_1:2-4:2"
