"""Tool plane: MCP -> provider schema translation (issue #78).

Pure functions over the Octave vocabularies of octave.mcp and
octave.inference. Neither subsystem imports the other; no SDK crosses
this boundary (guarded by tests/tools/test_package.py).
"""

from octave.tools.errors import ToolNameCollisionError, ToolTranslationError
from octave.tools.types import ProviderToolset, ToolRoute

__all__ = [
    "ProviderToolset",
    "ToolNameCollisionError",
    "ToolRoute",
    "ToolTranslationError",
]
