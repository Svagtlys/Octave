"""Octave agent orchestration exception hierarchy.

Same fail-loud philosophy as ``octave.inference.errors`` and
``octave.mcp.errors``: a stuck or misbehaving loop raises a named Octave
type rather than looping forever or failing silently.
"""

__all__ = ["AgentError", "ToolLoopMaxIterationsError"]


class AgentError(Exception):
    """Base class for every agent orchestration failure."""


class ToolLoopMaxIterationsError(AgentError):
    """The LLM kept requesting tools past the configured iteration limit."""

    def __init__(self, max_iterations: int) -> None:
        self.max_iterations = max_iterations
        super().__init__(f"Tool loop exceeded {max_iterations} iterations")
