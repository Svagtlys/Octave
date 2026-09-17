"""octave.agent exception hierarchy."""

from octave.agent.errors import AgentError, ToolLoopMaxIterationsError


def test_tool_loop_max_iterations_is_an_agent_error() -> None:
    error = ToolLoopMaxIterationsError(10)
    assert isinstance(error, AgentError)
    assert error.max_iterations == 10
    assert "10" in str(error)
