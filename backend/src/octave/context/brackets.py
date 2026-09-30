"""Reconstruct agent turn brackets from a transcript (issue #35).

The router's claim points are ephemeral — ``TurnRecord`` is returned in
``RouteOutcome.turns`` and never persisted. The transcript is the durable
record, and committed transcripts encode brackets exactly (design spec
Decision 3)::

    bracket = ( max(prev_agent_reply_seq, last_non_agent_event_seq), reply_seq ]

Walk seq order: agent-authored events open a region; that agent's
``assistant_message`` closes it. Non-agent events (user messages,
unauthored system notices) are anchors — excluded from every bracket.
A failed turn leaves orphan agent events with no closing reply: no
bracket, matching push semantics (the router emits no ``TurnRecord``
either). A task in ``tests/context/test_brackets.py`` pins this against
the shipped router's output.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from octave.db.models import Event
from octave.db.types import EventKind

__all__ = ["TurnBracket", "reconstruct_turns"]


@dataclass(frozen=True)
class TurnBracket:
    """One completed agent turn: agent-authored events ``seq_start..seq_end``
    inclusive, closed by the agent's reply at ``seq_end``. Deliberately not
    ``octave.agent.router.TurnRecord`` — no ``instance_id`` (archival keys on
    ``(session_id, agent_id, seq_range)``, #25 ADR) and no cross-plane import.
    """

    agent_id: str
    seq_start: int
    seq_end: int


def reconstruct_turns(
    events: Sequence[Event],
    agent_by_participant: Mapping[str, str],
) -> list[TurnBracket]:
    """Pure: seq-ordered events + participant→agent map → brackets.

    ``agent_by_participant`` maps participant id to agent id for agent
    participants only; events authored by anyone else (users, unauthored
    system notices) are anchors.
    """
    brackets: list[TurnBracket] = []
    anchor = 0  # seq of the last event that cannot belong to a bracket
    region_start: int | None = None  # start of the current agent-authored run
    for event in sorted(events, key=lambda e: e.seq):
        agent_id = agent_by_participant.get(event.author_participant_id or "")
        if agent_id is None:
            anchor = event.seq
            region_start = None
            continue
        if region_start is None:
            region_start = anchor + 1
        if event.kind == EventKind.ASSISTANT_MESSAGE:
            brackets.append(
                TurnBracket(
                    agent_id=agent_id, seq_start=region_start, seq_end=event.seq
                )
            )
            anchor = event.seq
            region_start = None
    return brackets
