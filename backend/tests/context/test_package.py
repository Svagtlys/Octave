"""Public API surface and plane-boundary posture of the context package."""

import ast
from pathlib import Path

import octave.context as context_pkg

SDK_MODULES = {"openai", "mcp"}
BANNED_OCTAVE_MODULES = {"agent"}


def _imported_top_level_octave_modules() -> set[str]:
    names: set[str] = set()
    for path in Path(context_pkg.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")
                if top[0] == "octave" and len(top) > 1:
                    names.add(top[1])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")
                    if top[0] == "octave" and len(top) > 1:
                        names.add(top[1])
    return names


def test_no_sdk_imports() -> None:
    """octave.context composes Octave façades only, never SDKs."""
    assert not _imported_top_level_octave_modules() & SDK_MODULES


def test_no_agent_plane_imports() -> None:
    """The CM plane must not import octave.agent (design spec Decision 9)."""
    assert not _imported_top_level_octave_modules() & BANNED_OCTAVE_MODULES


def test_public_names_are_exported() -> None:
    for name in (
        "AgentNotFound",
        "ArchiveReport",
        "ContextArchiver",
        "ContextBundle",
        "ContextInjector",
        "InjectedItem",
        "ModelBindingNotResolved",
        "ParticipantNotFound",
        "SessionNotFound",
        "TurnBracket",
        "reconstruct_turns",
    ):
        assert hasattr(context_pkg, name), name
