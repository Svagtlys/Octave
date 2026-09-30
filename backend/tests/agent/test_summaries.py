"""SessionSummarizer + digest strategies (design spec 2026-09-29, issue #28).

Digest tests are pure (synthetic Events, no DB); summarizer tests (Task 3+)
use the vec-enabled env fixture — VaultStore.upsert(embedding=None) calls
remove_vector, so the vec0 table must exist. The import header grows per
task; ruff's F401 gate runs at every commit, so import nothing early.
"""

from octave.db.models import Event
from octave.db.types import EventKind

DIM = 4


def _event(seq: int, kind: EventKind, content: str, author: str | None = None) -> Event:
    """Transient ORM row — digest rendering never touches the DB."""
    return Event(
        id=f"e_{seq}",
        session_id="s_1",
        seq=seq,
        kind=str(kind),
        author_participant_id=author,
        payload={"content": content},
    )


def _labels() -> dict[str, str]:
    return {"p_u1": "Alice", "p_a1": "Echo"}


async def test_digest_renders_whole_transcript_under_budget() -> None:
    from octave.agent.summaries import HeadTailDigest, SummaryContext

    events = [
        _event(1, EventKind.USER_MESSAGE, "hello", "p_u1"),
        _event(2, EventKind.ASSISTANT_MESSAGE, "hi there", "p_a1"),
    ]
    digest = await HeadTailDigest(head_events=2, tail_events=2).digest(
        SummaryContext(session_id="s_1", events=events, labels=_labels())
    )
    assert digest == "User: hello\nEcho: hi there"


async def test_digest_omits_middle_over_budget() -> None:
    from octave.agent.summaries import HeadTailDigest, SummaryContext

    events = [_event(i, EventKind.USER_MESSAGE, f"m{i}", "p_u1") for i in range(1, 8)]
    digest = await HeadTailDigest(head_events=2, tail_events=2).digest(
        SummaryContext(session_id="s_1", events=events, labels=_labels())
    )
    lines = digest.split("\n")
    assert lines[0] == "User: m1"
    assert lines[1] == "User: m2"
    assert lines[2] == "… 3 earlier events omitted …"
    assert lines[-2] == "User: m6"
    assert lines[-1] == "User: m7"


async def test_digest_renders_system_and_unknown_author() -> None:
    from octave.agent.summaries import HeadTailDigest, SummaryContext

    events = [
        _event(1, EventKind.SYSTEM, "Agent turn failed: boom"),
        _event(2, EventKind.ASSISTANT_MESSAGE, "orphan reply", "p_gone"),
    ]
    digest = await HeadTailDigest().digest(
        SummaryContext(session_id="s_1", events=events, labels=_labels())
    )
    assert digest == "System: Agent turn failed: boom\nAssistant: orphan reply"


async def test_digest_tool_events_are_compact_one_liners() -> None:
    from octave.agent.summaries import HeadTailDigest, SummaryContext

    call = _event(1, EventKind.TOOL_CALL, "")
    call.payload = {"tool_name": "calendar.list", "arguments": '{"month": "may"}'}
    result = _event(2, EventKind.TOOL_RESULT, "")
    result.payload = {"content": "x" * 500}
    digest = await HeadTailDigest(tool_line_chars=50).digest(
        SummaryContext(session_id="s_1", events=[call, result], labels=_labels())
    )
    lines = digest.split("\n")
    assert lines[0].startswith("Tool call: calendar.list")
    assert lines[1].startswith("Tool result: xxx")
    assert len(lines[1]) <= 50
