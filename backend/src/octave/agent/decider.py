"""Turn-taking decisions (design spec 2026-09-28).

The referee, not a player: a ``TurnDecider`` reads the roster + transcript
tail and names the next speaker (or hands control to the human). The
driver owns retry and fallback policy; deciders only raise
``DeciderChoiceError`` on unusable backend output.

``LlmTurnDecider`` is the default strategy. Decision models (Laya/Jev
style) arrive behind this same protocol in a follow-up issue — the driver
never learns which backend chose.
"""

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ValidationError

from octave.agent.errors import DeciderChoiceError
from octave.inference.adapter import InferenceAdapter
from octave.inference.types import CompletionRequest, Message

__all__ = ["Candidate", "Decision", "DecisionState", "LlmTurnDecider", "TurnDecider"]


@dataclass(frozen=True)
class Candidate:
    """One agent eligible to take the next turn."""

    instance_id: str
    participant_id: str
    label: str


@dataclass(frozen=True)
class DecisionState:
    """Decider input: the candidate roster + the transcript tail."""

    roster: list[Candidate] = field(default_factory=list)
    messages: list[Message] = field(default_factory=list)


class Decision(StrEnum):
    """Sentinel vocabulary for decider output. A decision is a plain
    ``str``: either ``Decision.AWAIT_USER`` or a candidate's
    ``participant_id`` (StrEnum members are strs, so the union collapses
    cleanly)."""

    AWAIT_USER = "await_user"


class TurnDecider(Protocol):
    """Next-speaker resolution. Implementations raise
    ``DeciderChoiceError`` on malformed/out-of-roster output."""

    async def decide(self, state: DecisionState) -> str:
        """Return ``Decision.AWAIT_USER`` or a participant_id from
        ``state.roster``."""
        ...


class _Choice(BaseModel):
    """Wire shape of the multi-agent decision: exactly one JSON object."""

    next: str


class LlmTurnDecider:
    """Default strategy over the chat ``InferenceAdapter`` (local-first).

    1:1 fast path (one candidate) requires no LLM call: user spoke last →
    the candidate speaks; the sole agent spoke last → await the human.
    Multi-agent asks for ``{"next": "<participant_id>" | "await_user"}``,
    roster-validated; malformed or phantom choices raise
    ``DeciderChoiceError`` (the driver retries once, then awaits user).
    """

    def __init__(
        self, *, adapter: InferenceAdapter, model: str | None = None
    ) -> None:
        self._adapter = adapter
        self._model = model

    async def decide(self, state: DecisionState) -> str:
        if len(state.roster) == 1:
            if state.messages and state.messages[-1].role == "assistant":
                return Decision.AWAIT_USER
            return state.roster[0].participant_id
        if not state.roster:
            return Decision.AWAIT_USER
        result = await self._adapter.complete(
            CompletionRequest(model=self._model, messages=self._prompt(state))
        )
        try:
            choice = _Choice.model_validate(json.loads(result.text)).next
        except (json.JSONDecodeError, ValidationError) as exc:
            raise DeciderChoiceError(
                f"decider output not parseable: {result.text!r}"
            ) from exc
        if choice == Decision.AWAIT_USER:
            return Decision.AWAIT_USER
        if any(candidate.participant_id == choice for candidate in state.roster):
            return choice
        raise DeciderChoiceError(f"decider chose unknown candidate: {choice!r}")

    @staticmethod
    def _prompt(state: DecisionState) -> list[Message]:
        roster = "\n".join(
            f"- {candidate.participant_id}: {candidate.label}"
            for candidate in state.roster
        )
        system = (
            "You are the turn referee for a multi-agent conversation. "
            "Decide who speaks next: one candidate participant_id, or "
            "await_user to hand control to the human. Base the choice on "
            "the conversation below. Reply with exactly one JSON object: "
            '{"next": "<participant_id>" | "await_user"}\n\n'
            f"Candidates:\n{roster}"
        )
        return [Message(role="system", content=system), *state.messages]
