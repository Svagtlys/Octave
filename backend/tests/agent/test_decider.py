"""LlmTurnDecider: 1:1 fast path + roster-validated JSON choice (spec #27)."""

import pytest

from octave.agent.decider import Candidate, Decision, DecisionState, LlmTurnDecider
from octave.agent.errors import DeciderChoiceError
from octave.inference.errors import AdapterConnectionError
from octave.inference.types import Message
from tests.agent.fakes import ScriptedAdapter, final_result


def _candidate(iid: str, pid: str) -> Candidate:
    return Candidate(instance_id=iid, participant_id=pid, label=f"Agent {iid}")


def _state(*messages: Message, roster: list[Candidate] | None = None) -> DecisionState:
    return DecisionState(
        roster=list(roster) if roster else [_candidate("i_1", "p_ai1")],
        messages=list(messages),
    )


async def test_fast_path_picks_sole_candidate_when_user_spoke_last() -> None:
    adapter = ScriptedAdapter([])  # any call fails the test via queue assert
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(Message(role="user", content="Hello"))
    assert await decider.decide(state) == "p_ai1"
    assert adapter.complete_calls == []


async def test_fast_path_awaits_user_when_agent_spoke_last() -> None:
    adapter = ScriptedAdapter([])
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(
        Message(role="user", content="Hello"),
        Message(role="assistant", content="Hi"),
    )
    assert await decider.decide(state) == Decision.AWAIT_USER
    assert adapter.complete_calls == []


async def test_fast_path_empty_transcript_picks_candidate() -> None:
    adapter = ScriptedAdapter([])
    decider = LlmTurnDecider(adapter=adapter)
    assert await decider.decide(_state()) == "p_ai1"


async def test_multi_agent_valid_choice() -> None:
    roster = [_candidate("i_1", "p_ai1"), _candidate("i_2", "p_ai2")]
    adapter = ScriptedAdapter([final_result('{"next": "p_ai2"}')])
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(Message(role="user", content="Hello"), roster=roster)
    assert await decider.decide(state) == "p_ai2"
    assert len(adapter.complete_calls) == 1


async def test_multi_agent_await_user_choice() -> None:
    roster = [_candidate("i_1", "p_ai1"), _candidate("i_2", "p_ai2")]
    adapter = ScriptedAdapter([final_result('{"next": "await_user"}')])
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(Message(role="user", content="Hello"), roster=roster)
    assert await decider.decide(state) == Decision.AWAIT_USER


async def test_multi_agent_malformed_output_raises() -> None:
    roster = [_candidate("i_1", "p_ai1"), _candidate("i_2", "p_ai2")]
    adapter = ScriptedAdapter([final_result("AI1 should go!")])
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(Message(role="user", content="Hello"), roster=roster)
    with pytest.raises(DeciderChoiceError):
        await decider.decide(state)


async def test_multi_agent_out_of_roster_choice_raises() -> None:
    roster = [_candidate("i_1", "p_ai1"), _candidate("i_2", "p_ai2")]
    adapter = ScriptedAdapter([final_result('{"next": "p_ghost"}')])
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(Message(role="user", content="Hello"), roster=roster)
    with pytest.raises(DeciderChoiceError):
        await decider.decide(state)


async def test_multi_agent_adapter_error_propagates() -> None:
    roster = [_candidate("i_1", "p_ai1"), _candidate("i_2", "p_ai2")]
    adapter = ScriptedAdapter([AdapterConnectionError("engine down")])
    decider = LlmTurnDecider(adapter=adapter)
    state = _state(Message(role="user", content="Hello"), roster=roster)
    with pytest.raises(AdapterConnectionError):
        await decider.decide(state)
