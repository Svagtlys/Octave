"""The archived corpus is findable through shipped VaultStore.search
(design spec §6: this item discharges 'future linked runs can query it').
"""

from octave.db.types import VaultKind
from octave.db.vault_store import VaultStore
from tests.context.fakes import FakeEmbedAdapter
from tests.context.test_archiver import _archive, _script_transcript, _seed


async def test_archived_chunks_are_searchable(env) -> None:
    await _seed(env)
    await _script_transcript(env)
    fake = FakeEmbedAdapter()
    await _archive(env, fake)
    adapter, factory = env
    async with factory() as session:
        store = VaultStore(adapter, session)
        # A query identical to the chunk content → identical vector → d=0.
        chunk = await store.get("transcript_chunk:s_1:2-4")
        assert chunk is not None
        hits = await store.search(
            user_id="u_1", embedding=fake.vector(chunk.content),
            kind=VaultKind.TRANSCRIPT_CHUNK,
        )
        assert [h.item.id for h in hits] == ["transcript_chunk:s_1:2-4"]
        # Session-scoped Tier-2 search (the aux-column filter from #32).
        scoped = await store.search(
            user_id="u_1", embedding=fake.vector(chunk.content),
            kind=VaultKind.TRANSCRIPT_CHUNK, session_id="s_1",
        )
        assert len(scoped) == 1
        wrong = await store.search(
            user_id="u_1", embedding=fake.vector(chunk.content),
            kind=VaultKind.TRANSCRIPT_CHUNK, session_id="s_other",
        )
        assert wrong == []
        # Other users never see them (search is always user-scoped).
        foreign = await store.search(
            user_id="u_2", embedding=fake.vector(chunk.content),
            kind=VaultKind.TRANSCRIPT_CHUNK,
        )
        assert foreign == []


async def test_embedded_summary_is_tier_one_findable(env) -> None:
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
    await _archive(env, fake)
    async with factory() as session:
        store = VaultStore(adapter, session)
        summary = await store.get("session_summary:s_1")
        assert summary is not None
        hits = await store.search(
            user_id="u_1", embedding=fake.vector(summary.content),
            kind=VaultKind.SESSION_SUMMARY,
        )
        assert [h.item.id for h in hits] == ["session_summary:s_1"]
