"""Context injection engine (issue #34).

Selects standing context — the session owner's preferences and skills, by
dual tagging (reserved ``global`` tag + intersection with the agent's
``effective_tags``) additively unioned with explicit by-name assignments —
and durably injects it once per (session, agent) as a ``context_injection``
transcript event (session-start cadence; ``every_turn`` is post-1.0.0,
design spec Decision 1).

Read-side only: vault reads are direct SELECTs (VaultStore owns the WRITE
invariants; nothing here mutates vault_items). Writes one event through the
shipped EventStore. Never commits — callers own transaction boundaries.
Never imports ``octave.agent`` (plane ban, Decision 9). Imports
``octave.inference`` types for context-window lookup only (issue #36) —
never completions.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.context.budget import (
    DEFAULT_CONTEXT_WINDOW,
    DEFAULT_CONTEXT_WINDOW_FRACTION,
    bundle_tokens,
    resolve_budget,
)
from octave.context.errors import (
    AgentNotFound,
    ContextBudgetExceededError,
    ParticipantNotFound,
    SessionNotFound,
)
from octave.db.binding import BindingNotResolved, resolve_binding
from octave.db.event_store import EventStore
from octave.db.models import (
    Agent,
    Event,
    Participant,
    Session,
    SessionParticipant,
    VaultItem,
)
from octave.db.types import (
    AgentAssignments,
    ContextInjectionPayload,
    EventKind,
    InjectedContextItem,
    ModelBinding,
    SelectionReason,
    VaultKind,
)
from octave.inference.adapter import InferenceAdapter
from octave.inference.errors import AdapterError

__all__ = [
    "ContextBundle",
    "ContextInjector",
    "InjectedItem",
    "SelectionReason",
]

logger = logging.getLogger(__name__)

_BINDING_ADAPTER: TypeAdapter[ModelBinding] = TypeAdapter(ModelBinding)

GLOBAL_TAG = "global"
"""Reserved item tag: inject for every agent (design spec Decision 3)."""

_PAGE = 100
_REASON_RANK: dict[SelectionReason, int] = {"explicit": 0, "global": 1, "agent_tag": 2}
_TRIM_RANK: dict[SelectionReason, int] = {"agent_tag": 0, "global": 1, "explicit": 2}
"""Budget drop-hint order: lowest precedence first (issue #36 Decision 5)."""
_SECTION_ORDER: dict[VaultKind, int] = {
    VaultKind.PROMPT: 0,
    VaultKind.PREFERENCE: 1,
    VaultKind.SKILL: 2,
}


@dataclass(frozen=True)
class InjectedItem:
    """One selected vault item with its provenance."""

    item_id: str
    kind: VaultKind
    name: str
    content: str
    reason: SelectionReason


@dataclass(frozen=True)
class ContextBundle:
    """Selection result for one (session, agent): prompt → preferences →
    skills, deduped by item id (highest-precedence reason wins), then
    (created_at, id)."""

    session_id: str
    agent_id: str
    items: list[InjectedItem]


@dataclass(frozen=True)
class _Candidate:
    item: VaultItem
    reason: SelectionReason


class ContextInjector:
    """Session-start context selection + idempotent injection event write.
    Constructed with the caller's AsyncSession; never commits."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        adapter_for: Callable[[str | None], InferenceAdapter] | None = None,
        tag_lookup: Callable[[str], str | None] | None = None,
        context_window_fraction: float = DEFAULT_CONTEXT_WINDOW_FRACTION,
        default_context_window: int = DEFAULT_CONTEXT_WINDOW,
    ) -> None:
        """``adapter_for=None`` keeps the budget guard on at the default
        window (no seam configured — cheapest wiring, Decision 7)."""
        if not 0 < context_window_fraction <= 1:
            raise ValueError("context_window_fraction must be in (0, 1]")
        if default_context_window < 1:
            raise ValueError("default_context_window must be >= 1")
        self._session = session
        self._events = EventStore(session)
        self._adapter_for = adapter_for
        self._tag_lookup = tag_lookup
        self._fraction = context_window_fraction
        self._default_window = default_context_window

    async def select(self, *, session_id: str, agent_id: str) -> ContextBundle:
        """Pure selection: no writes, no inference, no participant needed."""
        session_row = await self._session.get(Session, session_id)
        if session_row is None:
            raise SessionNotFound(session_id)
        agent = await self._session.get(Agent, agent_id)
        if agent is None:
            raise AgentNotFound(agent_id)
        assignments = self._parse_assignments(agent)
        agent_tags = {t for t in assignments.effective_tags} - {GLOBAL_TAG}
        owner = session_row.created_by_user_id

        candidates: dict[str, _Candidate] = {}
        if assignments.prompt is not None:
            await self._by_name(
                candidates, owner, VaultKind.PROMPT, assignments.prompt, "explicit"
            )
        await self._section(
            candidates,
            owner,
            VaultKind.PREFERENCE,
            assignments.preference_names,
            agent_tags,
        )
        await self._section(
            candidates, owner, VaultKind.SKILL, assignments.skills, agent_tags
        )

        ordered = sorted(
            candidates.values(),
            key=lambda c: (
                _SECTION_ORDER[VaultKind(c.item.kind)],
                _REASON_RANK[c.reason],
                c.item.created_at,
                c.item.id,
            ),
        )
        return ContextBundle(
            session_id=session_id,
            agent_id=agent_id,
            items=[
                InjectedItem(
                    item_id=c.item.id,
                    kind=VaultKind(c.item.kind),
                    name=c.item.name,
                    content=c.item.content,
                    reason=c.reason,
                )
                for c in ordered
            ],
        )

    async def ensure_injected(
        self, *, session_id: str, agent_id: str
    ) -> ContextBundle | None:
        """Idempotent session-start write: the agent's participant already
        holding a ``context_injection`` event in this session short-circuits
        to None (before any selection work). Otherwise append exactly one
        snapshot event (harness-authored: author NULL, targeted at the
        agent). Never commits."""
        session_row = await self._session.get(Session, session_id)
        if session_row is None:
            raise SessionNotFound(session_id)
        participant_id = await self._agent_participant(agent_id, session_id)
        existing = await self._session.execute(
            select(Event.id)
            .where(
                Event.session_id == session_id,
                Event.kind == str(EventKind.CONTEXT_INJECTION),
                Event.target_participant_id == participant_id,
            )
            .limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            return None
        bundle = await self.select(session_id=session_id, agent_id=agent_id)
        agent_row = await self._session.get(Agent, agent_id)
        assert agent_row is not None  # select() validated existence
        budget = resolve_budget(
            model_window=await self._model_context_window(agent_row),
            per_agent_budget=self._per_agent_budget(
                self._parse_assignments(agent_row)
            ),
            fraction=self._fraction,
        )
        estimated = bundle_tokens(bundle.items)
        if estimated > budget:
            raise ContextBudgetExceededError(
                session_id=session_id,
                agent_id=agent_id,
                budget_tokens=budget,
                estimated_tokens=estimated,
                # lowest precedence first: the trim order (Decision 5)
                drop_hint=sorted(
                    ((i.name, i.reason) for i in bundle.items),
                    key=lambda t: _TRIM_RANK[t[1]],
                ),
            )
        payload = ContextInjectionPayload(
            agent_id=agent_id,
            items=[
                InjectedContextItem(
                    item_id=i.item_id,
                    kind=str(i.kind),
                    name=i.name,
                    content=i.content,
                    reason=i.reason,
                )
                for i in bundle.items
            ],
            budget_tokens=budget,
            estimated_tokens=estimated,
        )
        await self._events.append(
            session_id,
            EventKind.CONTEXT_INJECTION,
            target_participant_id=participant_id,
            payload=payload.model_dump(),
        )
        return bundle

    def _parse_assignments(self, agent: Agent) -> AgentAssignments:
        """Lenient read (registry precedent): corrupt JSON → empty + warn."""
        try:
            return AgentAssignments.model_validate(agent.assignments or {})
        except ValidationError:
            logger.warning(
                "agent %s has invalid assignments; treating as empty", agent.id
            )
            return AgentAssignments()

    def _per_agent_budget(self, assignments: AgentAssignments) -> int | None:
        budget = assignments.token_budget
        if budget is None:
            return None
        if budget <= 0:
            logger.warning(
                "agent token_budget %r is not positive; treating as unset", budget
            )
            return None
        return budget

    async def _model_context_window(self, agent: Agent) -> int:
        """Reported context window for the agent's bound model; the
        configured default on any miss — the guard never silently off
        (issue #36 Decision 3)."""
        if self._adapter_for is None:
            return self._default_window
        if agent.model_binding is None:
            logger.warning(
                "agent %s has no model_binding; using default context window",
                agent.id,
            )
            return self._default_window
        try:
            binding = _BINDING_ADAPTER.validate_python(agent.model_binding)
            resolved = resolve_binding(binding, tag_lookup=self._tag_lookup)
        except (ValidationError, BindingNotResolved):
            logger.warning(
                "agent %s has unresolvable model_binding; using default "
                "context window",
                agent.id,
            )
            return self._default_window
        try:
            models = await self._adapter_for(resolved.adapter).list_models()
        except AdapterError:
            logger.warning(
                "context window lookup failed for model %s; using default",
                resolved.model,
            )
            return self._default_window
        for info in models:
            if info.id == resolved.model and info.context_window is not None:
                return info.context_window
        logger.warning(
            "model %s reports no context window; using default", resolved.model
        )
        return self._default_window

    async def _agent_participant(self, agent_id: str, session_id: str) -> str:
        """Resolve the agent's participant, requiring membership in the
        target session. ``events.target_participant_id`` carries a composite
        FK onto ``session_participants``; joining here turns a not-spawned-
        into-this-session agent into ParticipantNotFound instead of a raw
        IntegrityError at append time."""
        row = await self._session.execute(
            select(Participant.id)
            .join(
                SessionParticipant,
                SessionParticipant.participant_id == Participant.id,
            )
            .where(
                Participant.agent_id == agent_id,
                SessionParticipant.session_id == session_id,
            )
        )
        participant_id = row.scalar_one_or_none()
        if participant_id is None:
            raise ParticipantNotFound(agent_id)
        return participant_id

    async def _corpus(self, owner: str, kind: VaultKind) -> list[VaultItem]:
        """All owner's items of one kind, paged to exhaustion (a silent cap
        would be a correctness cliff)."""
        rows: list[VaultItem] = []
        offset = 0
        while True:
            page = list(
                (
                    await self._session.execute(
                        select(VaultItem)
                        .where(
                            VaultItem.user_id == owner, VaultItem.kind == str(kind)
                        )
                        .order_by(VaultItem.created_at, VaultItem.id)
                        .limit(_PAGE)
                        .offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            rows.extend(page)
            if len(page) < _PAGE:
                return rows
            offset += _PAGE

    async def _section(
        self,
        candidates: dict[str, _Candidate],
        owner: str,
        kind: VaultKind,
        names: list[str],
        agent_tags: set[str],
    ) -> None:
        for name in names:
            await self._by_name(candidates, owner, kind, name, "explicit")
        for item in await self._corpus(owner, kind):
            tags = self._tags_of(item)
            if GLOBAL_TAG in tags:
                self._add(candidates, item, "global")
            elif tags & agent_tags:
                self._add(candidates, item, "agent_tag")

    async def _by_name(
        self,
        candidates: dict[str, _Candidate],
        owner: str,
        kind: VaultKind,
        name: str,
        reason: SelectionReason,
    ) -> None:
        rows = list(
            (
                await self._session.execute(
                    select(VaultItem)
                    .where(
                        VaultItem.user_id == owner,
                        VaultItem.kind == str(kind),
                        VaultItem.name == name,
                    )
                    .order_by(VaultItem.created_at, VaultItem.id)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            logger.warning(
                "dangling assignment | kind=%s name=%r owner=%s", kind, name, owner
            )
            return
        if len(rows) > 1:
            logger.warning(
                "ambiguous name match | kind=%s name=%r ids=%s",
                kind,
                name,
                [r.id for r in rows],
            )
        for row in rows:
            self._add(candidates, row, reason)

    @staticmethod
    def _tags_of(item: VaultItem) -> set[str]:
        raw = item.meta.get("tags")
        if raw is None:
            return set()
        if not isinstance(raw, list) or not all(isinstance(t, str) for t in raw):
            logger.warning(
                "vault item %s has malformed meta.tags; treating as untagged",
                item.id,
            )
            return set()
        return {t.lower() for t in raw}

    @staticmethod
    def _add(
        candidates: dict[str, _Candidate], item: VaultItem, reason: SelectionReason
    ) -> None:
        existing = candidates.get(item.id)
        if existing is None or _REASON_RANK[reason] < _REASON_RANK[existing.reason]:
            candidates[item.id] = _Candidate(item=item, reason=reason)
