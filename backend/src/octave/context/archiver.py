"""Capture → embed → store for completed agent runs (issue #35).

Pull-only archival: ``archive(session_id)`` reconstructs turn brackets
from the transcript (:mod:`octave.context.brackets`), writes verbatim
``transcript_chunk`` items, and embeds Tier-1 ``session_summary`` plus
Tier-2 chunks through :class:`octave.db.vault_store.VaultStore`. The
archiver embeds only — it never generates prose
(``octave.agent.summaries.SessionSummarizer`` owns summary text; the
composition root runs ``collect()`` before ``archive()``).

Never commits; callers own transaction boundaries (``octave.db.deps``).
Embed API calls happen before any DB write, so inference latency never
sits inside a transaction. Idempotent: deterministic chunk ids +
"skip iff embedded by the current model" make re-runs no-ops.

``max_chars`` is the chunk safety valve: derive it from the embedding
model's documented INPUT context window (chars ≈ tokens × 3–4,
conservative). It is unrelated to ``embedding_dim`` (vector width).
Chunk boundaries freeze at first archival — changing ``max_chars``
never re-splits already-archived brackets (id exists → skip).
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.context.brackets import TurnBracket, reconstruct_turns
from octave.context.chunks import ChunkPlan, plan_chunks
from octave.context.errors import ModelBindingNotResolved, SessionNotFound
from octave.db.adapter import DbAdapter
from octave.db.event_store import EventStore
from octave.db.models import Event, Participant, Session, VaultItem
from octave.db.types import ExplicitModelBinding, ModelBinding, VaultKind
from octave.db.vault_store import VaultStore
from octave.inference.adapter import InferenceAdapter
from octave.inference.types import EmbeddingRequest

__all__ = ["ArchiveReport", "ContextArchiver", "DEFAULT_MAX_CHARS"]

logger = logging.getLogger(__name__)

#: Conservative default for common 8k-token embedding endpoints
#: (~2k tokens × ~3 chars/token). The composition root overrides per
#: deployed model; no auto-detection ships (design spec Decision 5).
DEFAULT_MAX_CHARS = 6000


@dataclass(frozen=True)
class ArchiveReport:
    """What one archive() pass did. For logging/tests; nothing persists it."""

    session_id: str
    turns: int
    summary_embedded: bool
    chunks_written: int
    chunks_skipped: int


def _resolve_binding(
    binding: ModelBinding,
    *,
    tag_lookup: Callable[[str], str | None] | None = None,
) -> tuple[str | None, str]:
    """(adapter_name_or_None, model). Duplicated from
    ``octave.agent.instances.resolve_model``: the plane ban forbids the
    import; a third consumer triggers extraction to a neutral home
    (design spec open question)."""
    if isinstance(binding, ExplicitModelBinding):
        return binding.adapter, binding.model
    if tag_lookup is None:
        raise ModelBindingNotResolved(
            f"tag binding {binding.tag!r} requires a tag_lookup"
        )
    model = tag_lookup(binding.tag)
    if model is None:
        raise ModelBindingNotResolved(f"model tag {binding.tag!r} resolves to nothing")
    return None, model


def _needs_embed(item: VaultItem | None, model: str) -> bool:
    """Absent, never embedded (#28 regeneration NULLs the cache), or
    embedded by a different model (config swap) → (re-)embed. Comparison
    is against the resolved binding model; ``embedding_model`` stores what
    the server reported (``result.model``, matching #28)."""
    if item is None:
        return True
    return item.embedding is None or item.embedding_model != model


class ContextArchiver:
    """Pull-only archival of completed agent runs. Never commits."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        db_adapter: DbAdapter,
        embed_binding: ModelBinding,
        adapter_for: Callable[[str | None], InferenceAdapter],
        tag_lookup: Callable[[str], str | None] | None = None,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be >= 1")
        self._session = session
        self._vault = VaultStore(db_adapter, session)
        self._events = EventStore(session)
        self._embed_binding = embed_binding
        self._adapter_for = adapter_for
        self._tag_lookup = tag_lookup
        self._max_chars = max_chars

    async def archive(self, session_id: str) -> ArchiveReport:
        session_row = await self._session.get(Session, session_id)
        if session_row is None:
            raise SessionNotFound(session_id)
        events = await self._events.read(session_id)
        brackets = reconstruct_turns(events, await self._agent_map(events))
        labels = await self._labels(brackets)

        plans: list[ChunkPlan] = []
        for bracket in brackets:
            bracket_events = [
                e for e in events if bracket.seq_start <= e.seq <= bracket.seq_end
            ]
            plans.extend(
                plan_chunks(
                    session_id=session_id,
                    bracket=bracket,
                    events=bracket_events,
                    label=labels.get(bracket.agent_id, bracket.agent_id),
                    max_chars=self._max_chars,
                )
            )
        summary = await self._vault.get(f"session_summary:{session_id}")

        adapter_name, model = _resolve_binding(
            self._embed_binding, tag_lookup=self._tag_lookup
        )
        existing: dict[str, VaultItem | None] = {}
        for plan in plans:
            existing[plan.item_id] = await self._vault.get(plan.item_id)
        pending = [p for p in plans if _needs_embed(existing[p.item_id], model)]
        summary_pending = summary is not None and _needs_embed(summary, model)
        if not pending and not summary_pending:
            return ArchiveReport(
                session_id=session_id,
                turns=len(brackets),
                summary_embedded=False,
                chunks_written=0,
                chunks_skipped=len(plans),
            )

        texts = [p.content for p in pending]
        if summary is not None and summary_pending:
            texts.append(summary.content)
        adapter = self._adapter_for(adapter_name)
        result = await adapter.embed(EmbeddingRequest(model=model, inputs=texts))
        if len(result.embeddings) != len(texts):
            raise ValueError(
                f"embed returned {len(result.embeddings)} vectors for "
                f"{len(texts)} inputs"
            )

        for plan, vector in zip(pending, result.embeddings):
            await self._vault.upsert(
                item_id=plan.item_id,
                user_id=session_row.created_by_user_id,
                kind=VaultKind.TRANSCRIPT_CHUNK,
                name=plan.name,
                content=plan.content,
                meta={**plan.meta, "model_name": result.model},
                embedding=vector,
                embedding_model=result.model,
            )
        if summary is not None and summary_pending:
            # read-modify-write: preserve #28's name/content/meta
            # (2026-09-29 ADR — one summary, two consumers).
            await self._vault.upsert(
                item_id=summary.id,
                user_id=summary.user_id,
                kind=VaultKind(summary.kind),
                name=summary.name,
                content=summary.content,
                meta=summary.meta,
                embedding=result.embeddings[-1],
                embedding_model=result.model,
            )

        logger.debug(
            "context archived | session=%s turns=%s chunks=%s skipped=%s "
            "summary=%s",
            session_id,
            len(brackets),
            len(pending),
            len(plans) - len(pending),
            summary_pending,
        )
        return ArchiveReport(
            session_id=session_id,
            turns=len(brackets),
            summary_embedded=bool(summary_pending),
            chunks_written=len(pending),
            chunks_skipped=len(plans) - len(pending),
        )

    async def _agent_map(self, events: Sequence[Event]) -> dict[str, str]:
        """participant_id -> agent_id for agent participants among authors."""
        participant_ids = {
            e.author_participant_id for e in events if e.author_participant_id
        }
        if not participant_ids:
            return {}
        rows = await self._session.execute(
            select(Participant.id, Participant.agent_id).where(
                Participant.id.in_(participant_ids),
                Participant.agent_id.is_not(None),
            )
        )
        return {pid: aid for pid, aid in rows.all()}

    async def _labels(self, brackets: Sequence[TurnBracket]) -> dict[str, str]:
        """agent_id -> display label (same Participant.label #28 renders)."""
        agent_ids = {b.agent_id for b in brackets}
        if not agent_ids:
            return {}
        rows = await self._session.execute(
            select(Participant.agent_id, Participant.label).where(
                Participant.agent_id.in_(agent_ids)
            )
        )
        return {aid: label for aid, label in rows.all() if aid is not None}
