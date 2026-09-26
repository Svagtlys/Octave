"""Public API surface and SDK-quarantine posture of the agent package."""

import ast
from pathlib import Path

import octave.agent as agent_pkg

SDK_MODULES = {"openai", "mcp"}


def _imported_top_level_modules() -> set[str]:
    names: set[str] = set()
    for path in Path(agent_pkg.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_no_sdk_imports() -> None:
    """octave.agent composes Octave façades only, never the openai/mcp SDKs."""
    assert not _imported_top_level_modules() & SDK_MODULES


def test_public_names_are_exported() -> None:
    for name in (
        "McpToolExecutor",
        "ToolError",
        "ToolExecutor",
        "ToolLoop",
        "ToolLoopLimitError",
        "ToolOutcome",
        "ToolTurn",
    ):
        assert hasattr(agent_pkg, name), name
