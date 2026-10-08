"""SessionSummarizer + digest strategies (design spec 2026-09-29, issue #28).

Digest tests are pure (synthetic Events, no DB); summarizer tests (Task 3+)
use the vec-enabled env fixture — VaultStore.upsert(embedding=None) calls
remove_vector, so the vec0 table must exist. The import header grows per
task; ruff's F401 gate runs at every commit, so import nothing early.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from octave.agent.summaries import SessionSummarizer
from octave.db.config import DbConfig
from octave.db.event_store import EventStore
from octave.db.models import Agent, Base, Event, Participant, Session, User, VaultItem
from octave.db.sqlite_adapter import SqliteVecAdapter
from octave.db.types import EventKind, ExplicitModelBinding, TagModelBinding, VaultKind
from octave.inference.types import CompletionResult

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


_TAG_BINDING = {"kind": "tag", "tag": "quick"}


@pytest_asyncio.fixture
async def env(
    tmp_path: Path,
) -> AsyncIterator[tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]]:
    """Vec-enabled DB with seeded user/agent/session/participants. Mirrors
    tests/db/test_vault_store.py's env: VaultStore.upsert(embedding=None)
    calls remove_vector, so the vec0 table must exist."""
    config = DbConfig(
        adapter="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'summaries.db'}",
        embedding_dim=DIM,
    )
    adapter = SqliteVecAdapter(config)
    engine: AsyncEngine = adapter.make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await adapter.ensure_vector_store(conn)
    factory = adapter.make_session_factory(engine)
    async with factory() as session:
        session.add(User(id="u_1", username="alice", display_name="Alice"))
        session.add(Agent(id="a_1", name="Echo", model_binding=_TAG_BINDING))
        session.add(Session(id="s_1", created_by_user_id="u_1", status="active"))
        session.add(Participant(id="p_u1", user_id="u_1", label="Alice"))
        session.add(Participant(id="p_a1", agent_id="a_1", label="Echo"))
        await session.commit()
    yield adapter, factory
    await engine.dispose()


def _explicit_binding() -> ExplicitModelBinding:
    return ExplicitModelBinding(kind="explicit", adapter="fake", model="summarizer-1")


class ScriptedAdapter:
    """Minimal in-test adapter: serves queued results, records requests.
    (tests/agent/fakes.py's ScriptedAdapter needs AdapterConfig; this one is
    duck-typed for complete() only.)"""

    def __init__(self, results: list[CompletionResult | Exception]) -> None:
        self._results = list(results)
        self.complete_calls = []

    async def complete(self, request):  # noqa: ANN001 — duck-typed seam
        self.complete_calls.append(request)
        if not self._results:
            raise AssertionError("ScriptedAdapter queue exhausted")
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _summarizer(
    session: AsyncSession,
    db_adapter: SqliteVecAdapter,
    scripted: ScriptedAdapter,
    **overrides,  # noqa: ANN001
) -> SessionSummarizer:
    return SessionSummarizer(
        session=session,
        db_adapter=db_adapter,
        binding=_explicit_binding(),
        adapter_for=lambda name: scripted,
        **overrides,
    )


async def _seed_transcript(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        store = EventStore(session)
        await store.append(
            "s_1",
            EventKind.USER_MESSAGE,
            author_participant_id="p_u1",
            payload={"content": "hello"},
        )
        await store.append(
            "s_1",
            EventKind.ASSISTANT_MESSAGE,
            author_participant_id="p_a1",
            payload={"content": "hi there"},
        )
        await session.commit()


def _completion(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="summarizer-1", finish_reason="stop")


async def test_peek_returns_none_when_absent(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    async with factory() as session:
        summarizer = _summarizer(session, adapter, ScriptedAdapter([]))
        assert await summarizer.peek("s_1") is None


async def test_collect_raises_session_not_found(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    from octave.agent.errors import SessionNotFoundError

    adapter, factory = env
    async with factory() as session:
        summarizer = _summarizer(session, adapter, ScriptedAdapter([]))
        with pytest.raises(SessionNotFoundError):
            await summarizer.collect("s_missing")


async def test_collect_empty_transcript_returns_none(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    scripted = ScriptedAdapter([])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        assert await summarizer.collect("s_1") is None
    assert scripted.complete_calls == []


async def test_collect_generates_persists_and_returns(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter(
        [_completion("Title: Greeting\nAlice greeted; Echo replied.")]
    )
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        summary = await summarizer.collect("s_1")
        await session.commit()
    assert summary is not None
    assert summary.title == "Greeting"
    assert summary.content == "Alice greeted; Echo replied."
    assert summary.covered_seq == 2
    assert summary.owner_user_id == "u_1"
    assert summary.model_name == "summarizer-1"

    # The vault item landed with deterministic id, kind, meta, no embedding.
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        item = await VaultStore(adapter, session).get("session_summary:s_1")
    assert item is not None
    assert item.kind == "session_summary"
    assert item.user_id == "u_1"
    assert item.meta == {
        "session_id": "s_1",
        "covered_seq": 2,
        "model_name": "summarizer-1",
    }
    assert item.embedding is None

    # One completion: system instruction + digest with participant labels.
    assert len(scripted.complete_calls) == 1
    request = scripted.complete_calls[0]
    assert request.model == "summarizer-1"
    assert request.messages[0].role == "system"
    assert "Title:" in request.messages[0].content
    assert request.messages[1].content == "User: hello\nEcho: hi there"


async def test_peek_reads_item_written_by_vault_store(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    from octave.db.vault_store import VaultStore

    adapter, factory = env
    async with factory() as session:
        await VaultStore(adapter, session).upsert(
            item_id="session_summary:s_1",
            user_id="u_1",
            kind=VaultKind.SESSION_SUMMARY,
            name="Greeting",
            content="Alice greeted; Echo replied.",
            meta={"session_id": "s_1", "covered_seq": 2, "model_name": "summarizer-1"},
        )
        await session.commit()
    async with factory() as session:
        summarizer = _summarizer(session, adapter, ScriptedAdapter([]))
        summary = await summarizer.peek("s_1")
    assert summary is not None
    assert summary.title == "Greeting"
    assert summary.content == "Alice greeted; Echo replied."
    assert summary.covered_seq == 2
    assert summary.owner_user_id == "u_1"
    assert summary.model_name == "summarizer-1"
    assert summary.session_id == "s_1"


async def test_peek_treats_malformed_meta_as_missing(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    async with factory() as session:
        session.add(
            VaultItem(
                id="session_summary:s_1",
                user_id="u_1",
                kind="session_summary",
                name="x",
                content="y",
                meta={"nope": True},
            )
        )
        await session.commit()
    async with factory() as session:
        summarizer = _summarizer(session, adapter, ScriptedAdapter([]))
        assert await summarizer.peek("s_1") is None


async def test_collect_returns_cached_when_fresh(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter([_completion("Title: Greeting\nfirst.")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        await summarizer.collect("s_1")
        await session.commit()
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)  # queue empty
        summary = await summarizer.collect("s_1")
    assert summary is not None
    assert summary.content == "first."
    assert len(scripted.complete_calls) == 1  # no second call


async def test_collect_regenerates_when_transcript_grew(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter(
        [_completion("Title: Greeting\nfirst."), _completion("Title: Later\nsecond.")]
    )
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        await summarizer.collect("s_1")
        await session.commit()
    async with factory() as session:
        await EventStore(session).append(
            "s_1",
            EventKind.USER_MESSAGE,
            author_participant_id="p_u1",
            payload={"content": "and then?"},
        )
        await session.commit()
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        summary = await summarizer.collect("s_1")
        await session.commit()
    assert summary is not None
    assert summary.content == "second."
    assert summary.covered_seq == 3
    assert len(scripted.complete_calls) == 2
    async with factory() as session:
        fresh = _summarizer(session, adapter, ScriptedAdapter([]))
        peeked = await fresh.peek("s_1")
    assert peeked is not None
    assert peeked.content == "second."  # same deterministic item id


async def test_collect_force_regenerates_even_when_fresh(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter(
        [
            _completion("Title: Greeting\nfirst."),
            _completion("Title: Greeting\nrewritten."),
        ]
    )
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        await summarizer.collect("s_1")
        second = await summarizer.collect("s_1", force=True)
        await session.commit()
    assert second is not None
    assert second.content == "rewritten."
    assert len(scripted.complete_calls) == 2


async def test_collect_never_commits(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter([_completion("Title: Greeting\nbody.")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        await summarizer.collect("s_1")
        await session.rollback()
    async with factory() as session:
        summarizer = _summarizer(session, adapter, ScriptedAdapter([]))
        assert await summarizer.peek("s_1") is None


async def test_adapter_error_propagates_without_partial_write(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    from octave.inference.errors import AdapterConnectionError

    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter([AdapterConnectionError("engine down")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        with pytest.raises(AdapterConnectionError):
            await summarizer.collect("s_1")
        await session.rollback()
    async with factory() as session:
        peeked = await _summarizer(session, adapter, ScriptedAdapter([])).peek("s_1")
    assert peeked is None


async def test_empty_completion_raises_summary_error(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    from octave.agent.errors import SummaryError

    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter([_completion("   \n  ")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        with pytest.raises(SummaryError):
            await summarizer.collect("s_1")
        await session.rollback()
    async with factory() as session:
        peeked = await _summarizer(session, adapter, ScriptedAdapter([])).peek("s_1")
    assert peeked is None


async def test_explicit_binding_passes_adapter_name_to_adapter_for(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    seen: list[str | None] = []
    scripted = ScriptedAdapter([_completion("Title: T\nbody.")])

    def adapter_for(name: str | None) -> ScriptedAdapter:
        seen.append(name)
        return scripted

    async with factory() as session:
        summarizer = SessionSummarizer(
            session=session,
            db_adapter=adapter,
            binding=_explicit_binding(),
            adapter_for=adapter_for,
        )
        await summarizer.collect("s_1")
    assert seen == ["fake"]  # ExplicitModelBinding(adapter="fake")


async def test_tag_binding_without_lookup_fails_loud(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    from octave.agent.errors import ModelBindingError

    adapter, factory = env
    await _seed_transcript(factory)
    async with factory() as session:
        summarizer = SessionSummarizer(
            session=session,
            db_adapter=adapter,
            binding=TagModelBinding(kind="tag", tag="quick"),
            adapter_for=lambda name: ScriptedAdapter([]),
        )
        with pytest.raises(ModelBindingError):
            await summarizer.collect("s_1")


async def test_title_falls_back_to_session_title(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    async with factory() as session:
        session_row = await session.get(Session, "s_1")
        session_row.title = "Weekly review"
        await session.commit()
    scripted = ScriptedAdapter([_completion("no title line here\nBody prose.")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        summary = await summarizer.collect("s_1")
    assert summary is not None
    assert summary.title == "Weekly review"
    assert summary.content == "no title line here\nBody prose."


async def test_title_falls_back_to_first_user_message(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    await _seed_transcript(factory)
    scripted = ScriptedAdapter([_completion("Body only, no title contract.")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        summary = await summarizer.collect("s_1")
    assert summary is not None
    assert summary.title == "hello"  # first user message content


async def test_title_falls_back_to_session_id_when_no_user_message(
    env: tuple[SqliteVecAdapter, async_sessionmaker[AsyncSession]]
) -> None:
    adapter, factory = env
    async with factory() as session:
        await EventStore(session).append(
            "s_1", EventKind.SYSTEM, payload={"content": "system only"}
        )
        await session.commit()
    scripted = ScriptedAdapter([_completion("Body.")])
    async with factory() as session:
        summarizer = _summarizer(session, adapter, scripted)
        summary = await summarizer.collect("s_1")
    assert summary is not None
    assert summary.title == "Session s_1"  # f"Session {id[:8]}" — "s_1" is 3 chars


async def test_digest_zero_tail_does_not_slice_whole_transcript() -> None:
    from octave.agent.summaries import HeadTailDigest, SummaryContext

    events = [
        _event(seq, EventKind.USER_MESSAGE, f"msg {seq}", "p_u1")
        for seq in range(1, 6)
    ]
    digest = await HeadTailDigest(head_events=1, tail_events=0).digest(
        SummaryContext(session_id="s_1", events=events, labels=_labels())
    )
    lines = digest.splitlines()
    assert lines[0] == "User: msg 1"
    assert "4 earlier events omitted" in lines[-1]
    assert len(lines) == 2  # head + marker only; tail_events=0 contributes nothing


def test_digest_rejects_negative_counts() -> None:
    from octave.agent.summaries import HeadTailDigest

    with pytest.raises(ValueError):
        HeadTailDigest(head_events=-1)
    with pytest.raises(ValueError):
        HeadTailDigest(tail_events=-1)
    with pytest.raises(ValueError):
        HeadTailDigest(tool_line_chars=0)
