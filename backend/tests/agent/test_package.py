"""Public API surface of octave.agent — the caller-facing vocabulary."""

import octave.agent as agent_pkg


def test_public_names_are_exported() -> None:
    for name in ("run_tool_loop", "AgentError", "ToolLoopMaxIterationsError"):
        assert hasattr(agent_pkg, name), name
