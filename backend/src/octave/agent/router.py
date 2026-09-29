"""Session-level message routing (design spec 2026-09-28, issue #27).

The driver: append the user message, then loop — decider picks an idle
instance, ``begin_turn`` claims the turn (the #25 mutex), the injected
``TurnRunner`` port produces the reply, the reply is recorded, the turn is
released — until await-user, the hop limit, or a runner failure.

Delivery is via the shared transcript (spec decision 2): there are no
per-agent queues; an agent's "message plus everything queued for it" is
the transcript through the current seq, which the runner reads as input.
Routing itself is ephemeral — ``events.target_participant_id`` stays NULL.

Follows the VaultStore convention: constructed with the caller's
AsyncSession, never commits. One ``deliver()`` is one caller-owned
transaction; the whole exchange commits atomically or not at all.
"""

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.agent.decider import Candidate, Decision, DecisionState, TurnDecider
from octave.agent.errors import DeciderChoiceError, NotAMemberError
from octave.agent.instances import AgentInstanceManager
from octave.agent.registry import AgentRegistry, RunningAgent
from octave.db.event_store import EventStore
from octave.db.models import Event, Participant, SessionParticipant
from octave.db.types import AgentStatus, EventKind, InstanceStatus
from octave.inference.errors import AdapterError
from octave.inference.types import Message

__all__ = [
    "MessageRouter",
    "RouteOutcome",
    "StopReason",
    "TurnRecord",
    "TurnRunner",
]

logger = logging.getLogger(__name__)


class StopReason(StrEnum):
    """Why ``deliver()`` returned control to the caller."""

    AWAIT_USER = "await_user"
    HOP_LIMIT = "hop_limit"
    ERROR = "error"


@dataclass(frozen=True)
class TurnRecord:
    """One completed agent turn. The seq range is the whole claimed bracket:
    events the runner appends during the turn (tool calls/results once
    Integration #1 lands) through the reply event. CM #5's archival key per
    #25: (session_id, agent_id, seq_range) — never instance_id."""

    session_id: str
    agent_id: str
    instance_id: str
    seq_start: int
    seq_end: int


@dataclass(frozen=True)
class RouteOutcome:
    turns: list[TurnRecord] = field(default_factory=list)
    stop_reason: StopReason = StopReason.AWAIT_USER
    error: str | None = None


class TurnRunner(Protocol):
    """What happens inside a claimed turn. The real runner (context
    assembly + ToolLoop over the instance's model binding) is Integration
    #1's composition — mirrors the ToolExecutor/McpToolExecutor split.
    Returns the final reply text."""

    async def run_turn(
        self, *, instance: RunningAgent, messages: list[Message]
    ) -> str: ...


class MessageRouter:
    """Session-level turn-taking driver. Never commits; the caller owns
    the transaction boundary."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        manager: AgentInstanceManager,
        registry: AgentRegistry,
        decider: TurnDecider,
        turn_runner: TurnRunner,
        max_agent_turns: int = 4,
        decider_tail: int = 30,
    ) -> None:
        self._session = session
        self._manager = manager
        self._registry = registry
        self._decider = decider
        self._turn_runner = turn_runner
        self._max_agent_turns = max_agent_turns
        self._decider_tail = decider_tail
        self._events = EventStore(session)

    async def deliver(
        self, session_id: str, *, author_participant_id: str, content: str
    ) -> RouteOutcome:
        """Record the user message and drive agent turns until stop."""
        await self._require_membership(session_id, author_participant_id)
        await self._events.append(
            session_id,
            EventKind.USER_MESSAGE,
            author_participant_id=author_participant_id,
            payload={"content": content},
        )
        turns: list[TurnRecord] = []
        agent_turns = 0
        messages: list[Message] = []
        cursor = 0
        while True:
            roster = await self._roster(session_id)
            if not roster:
                return RouteOutcome(turns=turns, stop_reason=StopReason.AWAIT_USER)
            if agent_turns >= self._max_agent_turns:
                return RouteOutcome(turns=turns, stop_reason=StopReason.HOP_LIMIT)
            # Incremental transcript read: only events past the cursor (the
            # runner's in-turn appends included). The whole exchange is one
            # transaction on one connection, so the cursor never misses a row.
            new_events = await self._events.read(session_id, after_seq=cursor)
            if new_events:
                cursor = new_events[-1].seq
                messages.extend(_transcript_messages(new_events))
            state = DecisionState(
                roster=roster, messages=messages[-self._decider_tail :]
            )
            choice = await self._decide_with_fallback(state)
            if choice == Decision.AWAIT_USER:
                return RouteOutcome(turns=turns, stop_reason=StopReason.AWAIT_USER)
            candidate = next(c for c in roster if c.participant_id == choice)
            # The bracket opens at claim: the driver is the only writer in
            # this transaction, so the next seq after the cursor is ours.
            outcome = await self._run_agent_turn(
                session_id, candidate, messages, turns, bracket_start=cursor + 1
            )
            if outcome is not None:
                return outcome
            agent_turns += 1

    async def _run_agent_turn(
        self,
        session_id: str,
        candidate: Candidate,
        messages: list[Message],
        turns: list[TurnRecord],
        *,
        bracket_start: int,
    ) -> RouteOutcome | None:
        """Claim, run, record, release. None = turn completed normally;
        a RouteOutcome = the loop must stop (runner failure)."""
        # TurnInProgressError propagates: concurrent drivers on one
        # session is a caller bug; the mutex is fail-loud by design.
        await self._manager.begin_turn(candidate.instance_id)
        running = await self._registry.get_instance(candidate.instance_id)
        try:
            reply_text = await self._turn_runner.run_turn(
                instance=running, messages=messages
            )
        except Exception as exc:  # noqa: BLE001 — turn failures land idle
            logger.warning(
                "agent turn failed | instance=%s error=%s", candidate.instance_id, exc
            )
            await self._manager.end_turn(candidate.instance_id)
            await self._events.append(
                session_id,
                EventKind.SYSTEM,
                payload={"content": f"Agent turn failed: {exc}"},
            )
            return RouteOutcome(
                turns=turns, stop_reason=StopReason.ERROR, error=str(exc)
            )
        reply = await self._events.append(
            session_id,
            EventKind.ASSISTANT_MESSAGE,
            author_participant_id=candidate.participant_id,
            payload={"content": reply_text},
        )
        await self._manager.end_turn(candidate.instance_id)
        turns.append(
            TurnRecord(
                session_id=session_id,
                agent_id=running.agent_id,
                instance_id=candidate.instance_id,
                seq_start=bracket_start,
                seq_end=reply.seq,
            )
        )
        return None

    async def _decide_with_fallback(self, state: DecisionState) -> str:
        """One retry on invalid output (DeciderChoiceError) or adapter
        failure (AdapterError), then AWAIT_USER: a confused or unreachable
        referee hands control to the human — the user message is already
        appended and must survive, never crash the session."""
        for attempt in (0, 1):
            try:
                return await self._decider.decide(state)
            except (DeciderChoiceError, AdapterError) as exc:
                logger.warning(
                    "decider failed | attempt=%s error=%s", attempt, exc
                )
        return Decision.AWAIT_USER

    async def _roster(self, session_id: str) -> list[Candidate]:
        """Idle instances of active definitions, joined to their
        participant identity (guaranteed by spawn; a missing participant
        row is write-path corruption — surface it loud)."""
        running = [
            record
            for record in await self._registry.list_instances(
                session_id=session_id, status=InstanceStatus.IDLE
            )
            if record.definition_status == AgentStatus.ACTIVE
        ]
        if not running:
            return []
        rows = await self._session.execute(
            select(Participant.agent_id, Participant.id).where(
                Participant.agent_id.in_([record.agent_id for record in running])
            )
        )
        # agent_id is nullable at the column level (participant supertype);
        # a NULL here would be write-path corruption, so drop it silently —
        # the dict lookup below then surfaces the missing identity loudly.
        participant_by_agent: dict[str, str] = {
            agent_id: participant_id
            for agent_id, participant_id in rows.all()
            if agent_id is not None
        }
        return [
            Candidate(
                instance_id=record.instance_id,
                participant_id=participant_by_agent[record.agent_id],
                label=record.agent_name,
            )
            for record in running
        ]

    async def _require_membership(
        self, session_id: str, participant_id: str
    ) -> None:
        """App-level author gate: current membership (the events FK covers
        identity, not membership)."""
        row = await self._session.execute(
            select(SessionParticipant).where(
                SessionParticipant.session_id == session_id,
                SessionParticipant.participant_id == participant_id,
                SessionParticipant.left_at.is_(None),
            )
        )
        if row.scalar_one_or_none() is None:
            raise NotAMemberError(
                f"participant {participant_id} is not a current member of {session_id}"
            )


def _transcript_messages(events: list[Event]) -> list[Message]:
    """Transcript → chat messages. Tool/system entries are skipped with a
    debug log: they are context for humans and archival, not chat history."""
    messages: list[Message] = []
    for event in events:
        if event.kind == EventKind.USER_MESSAGE:
            messages.append(Message(role="user", content=event.payload["content"]))
        elif event.kind == EventKind.ASSISTANT_MESSAGE:
            messages.append(
                Message(role="assistant", content=event.payload["content"])
            )
        else:
            logger.debug(
                "skipping non-message event | kind=%s seq=%s", event.kind, event.seq
            )
    return messages
