"""Agent loop vocabulary: defaults and provenance of ToolOutcome/ToolTurn."""

from octave.agent.types import ToolOutcome, ToolTurn
from octave.inference.types import CompletionResult, Message


def test_tool_outcome_defaults() -> None:
    outcome = ToolOutcome(content="ok")
    assert outcome.is_error is False


def test_tool_turn_round_trip() -> None:
    turn = ToolTurn(
        messages=[
            Message(role="user", content="hi"),
            Message(role="assistant", content="yo"),
        ],
        result=CompletionResult(text="yo", model="m"),
        tool_rounds=0,
    )
    assert ToolTurn.model_validate(turn.model_dump()) == turn
