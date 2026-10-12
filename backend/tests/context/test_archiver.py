"""ContextArchiver: capture → embed → store (design spec, issue #35).

Real SQLite via the env fixture; FakeEmbedAdapter implements embed only.
Transcripts are scripted through EventStore (the router's write path).
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from octave.context import ContextArchiver, SessionNotFound
from octave.context.archiver import _needs_embed
from octave.db.event_store import EventStore
from octave.db.models import Agent, Participant, Session, User
from octave.db.sqlite_adapter import SqliteVecAdapter, vector_table_name
from octave.db.types import EventKind, ExplicitModelBinding, VaultKind
from octave.db.vault_store import VaultStore
from octave.inference.errors import AdapterConnectionError
from tests.context.fakes import FakeEmbedAdapter

_BINDING = ExplicitModelBinding(kind="explicit", adapter="fake", model="embed-fake")

Env = tuple[SqliteVecAdapter, async_sessionmaker]


async def _seed(env: Env) -> None:
    _, factory = env
    async with factory() as s:
        s.add(User(id="u_1", username="alice", display_name="Alice"))
        s.add(User(id="u_2", username="bob", display_name="Bob"))
        s.add(
            Agent(id="a_1", name="Echo", model_binding={"kind": "tag", "tag": "quick"})
        )
        s.add(Session(id="s_1", created_by_user_id="u_1", status="active"))
        s.add(Participant(id="p_u1", user_id="u_1", label="Alice"))
        s.add(Participant(id="p_a1", agent_id="a_1", label="Echo"))
        await s.commit()


async def _script_transcript(env: Env, *, turns: int = 1) -> None:
    """One turn = tool_call + tool_result + assistant reply; first turn
    preceded by the user trigger."""
    _, factory = env
    async with factory() as s:
        store = EventStore(s)
        await store.append(
            "s_1", EventKind.USER_MESSAGE, author_participant_id="p_u1",
            payload={"content": "weather?"},
        )
        for i in range(turns):
            await store.append(
                "s_1", EventKind.TOOL_CALL, author_participant_id="p_a1",
                payload={"tool_name": "web.search", "arguments": f'{{"q": "q{i}"}}'},
            )
            await store.append(
                "s_1", EventKind.TOOL_RESULT, author_participant_id="p_a1",
                payload={"content": f"Sunny 21C run {i}"},
            )
            await store.append(
                "s_1", EventKind.ASSISTANT_MESSAGE, author_participant_id="p_a1",
                payload={"content": f"It is sunny, run {i}."},
            )
        await s.commit()


async def _archive(
    env: Env, fake: FakeEmbedAdapter, session_id: str = "s_1", **kwargs
):
    adapter, factory = env
    async with factory() as session:
        archiver = ContextArchiver(
            session=session, db_adapter=adapter, embed_binding=_BINDING,
            adapter_for=lambda name: fake, **kwargs,
        )
        report = await archiver.archive(session_id)
        await session.commit()
        return report


async def _chunk_items(env: Env):
    adapter, factory = env
    async with factory() as session:
        return await VaultStore(adapter, session).list_items(
            user_id="u_1", kind=VaultKind.TRANSCRIPT_CHUNK
        )


async def test_happy_path_writes_embedded_chunks(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter()
    report = await _archive(env, fake)
    assert report.turns == 1
    assert report.chunks_written == 1
    assert report.summary_embedded is False
    items = await _chunk_items(env)
    assert [i.id for i in items] == ["transcript_chunk:s_1:2-4"]
    item = items[0]
    assert item.embedding is not None
    assert item.embedding_model == "embed-fake"
    assert item.meta["agent_id"] == "a_1"
    assert item.meta["seq_start"] == 2 and item.meta["seq_end"] == 4
    assert len(fake.embed_calls) == 1


async def test_idempotent_second_pass_is_a_noop(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter()
    await _archive(env, fake)
    report = await _archive(env, fake)
    assert report.chunks_written == 0
    assert report.chunks_skipped == 1
    assert len(fake.embed_calls) == 1  # second pass: no embed call


async def test_multi_turn_single_batched_embed(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env, turns=2)
    fake = FakeEmbedAdapter()
    report = await _archive(env, fake)
    assert report.turns == 2
    assert report.chunks_written == 2
    assert len(fake.embed_calls) == 1
    assert len(fake.embed_calls[0].inputs) == 2


async def test_summary_without_embedding_gets_embedded(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    adapter, factory = env
    async with factory() as session:
        await VaultStore(adapter, session).upsert(
            item_id="session_summary:s_1", user_id="u_1",
            kind=VaultKind.SESSION_SUMMARY, name="Weather check",
            content="Checked the weather: sunny.",
            meta={"session_id": "s_1", "covered_seq": 4, "model_name": "chat-1"},
        )
        await session.commit()
    fake = FakeEmbedAdapter()
    report = await _archive(env, fake)
    assert report.summary_embedded is True
    async with factory() as session:
        summary = await VaultStore(adapter, session).get("session_summary:s_1")
    assert summary is not None
    assert summary.embedding is not None
    assert summary.name == "Weather check"  # read-modify-write preserves prose
    assert summary.meta["covered_seq"] == 4


async def test_summary_absent_skips_tier_one(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter()
    report = await _archive(env, fake)
    assert report.summary_embedded is False


async def test_model_swap_reembeds(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    await _archive(env, FakeEmbedAdapter())
    # new binding model: staleness compares against the resolved model
    adapter, factory = env

    async with factory() as session:
        archiver = ContextArchiver(
            session=session, db_adapter=adapter,
            embed_binding=ExplicitModelBinding(
                kind="explicit", adapter="fake", model="embed-new"
            ),
            adapter_for=lambda name: FakeEmbedAdapter(model="embed-new"),
        )
        report = await archiver.archive("s_1")
        await session.commit()
    assert report.chunks_written == 1  # stale by embedding_model mismatch


async def test_max_chars_split_writes_parted_items(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter()
    report = await _archive(env, fake, max_chars=40)
    assert report.chunks_written > 1
    items = await _chunk_items(env)
    ids = sorted(i.id for i in items)
    assert ids[0] == "transcript_chunk:s_1:2-4:1"
    assert all(i.embedding is not None for i in items)


async def test_embed_failure_propagates_with_no_writes(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter()

    async def _boom(request):
        raise AdapterConnectionError("down")

    fake.embed = _boom  # type: ignore[method-assign]
    with pytest.raises(AdapterConnectionError):
        await _archive(env, fake)
    assert await _chunk_items(env) == []


async def test_dimension_mismatch_propagates(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter(dim=8)  # adapter config is DIM=4
    with pytest.raises(BaseException) as excinfo:  # DbDimensionMismatchError
        await _archive(env, fake)
    assert "DbDimensionMismatchError" in type(excinfo.value).__name__


async def test_unknown_session_raises(env: Env) -> None:
    await _seed(env)
    with pytest.raises(SessionNotFound):
        await _archive(env, FakeEmbedAdapter(), session_id="s_missing")


async def test_rollback_leaves_vault_and_vec_clean(env: Env) -> None:
    await _seed(env)
    await _script_transcript(env)
    adapter, factory = env
    async with factory() as session:
        archiver = ContextArchiver(
            session=session, db_adapter=adapter, embed_binding=_BINDING,
            adapter_for=lambda name: FakeEmbedAdapter(),
        )
        await archiver.archive("s_1")
        await session.rollback()
    async with factory() as checker:
        items = await VaultStore(adapter, checker).list_items(
            user_id="u_1", kind=VaultKind.TRANSCRIPT_CHUNK
        )
        assert items == []
        vec_count = (
            await checker.execute(
                text(f"SELECT count(*) FROM {vector_table_name(4)}")
            )
        ).scalar_one()
        assert vec_count == 0


def test_needs_embed_rules() -> None:
    class _Item:
        def __init__(self, embedding, embedding_model):
            self.embedding = embedding
            self.embedding_model = embedding_model

    assert _needs_embed(None, "m") is True
    assert _needs_embed(_Item(None, None), "m") is True
    assert _needs_embed(_Item(b"vec", "other"), "m") is True
    assert _needs_embed(_Item(b"vec", "m"), "m") is False
