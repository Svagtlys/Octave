"""Public API surface and SDK-quarantine posture of the tool plane."""

import ast
from pathlib import Path

import octave.tools as tools_pkg
from octave.tools.errors import ToolError, ToolNameCollisionError, ToolTranslationError

SDK_MODULES = {"openai", "mcp"}


def _imported_top_level_modules() -> set[str]:
    names: set[str] = set()
    for path in Path(tools_pkg.__file__).parent.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_no_sdk_imports() -> None:
    """octave.tools touches Octave façades only, never the openai/mcp SDKs."""
    assert not _imported_top_level_modules() & SDK_MODULES


def test_public_names_are_exported() -> None:
    for name in (
        "ProviderToolset",
        "ToolError",
        "ToolRoute",
        "ToolTranslationError",
        "ToolNameCollisionError",
        "translate_tools",
    ):
        assert hasattr(tools_pkg, name), name


def test_tool_error_hierarchy() -> None:
    assert issubclass(ToolTranslationError, ToolError)
    assert issubclass(ToolNameCollisionError, ToolTranslationError)
