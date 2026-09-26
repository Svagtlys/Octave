"""Agent plane: tool-use orchestration loop (issue #79).

Composition layer — the only package importing both octave.mcp and
octave.inference. Future Agent Manager components (manager, registry,
router) land here as additional modules; they do not exist yet.
"""

from octave.agent.errors import ToolLoopLimitError
from octave.agent.executor import ToolExecutor
from octave.agent.loop import ToolLoop
from octave.agent.mcp_executor import McpToolExecutor
from octave.agent.types import ToolOutcome, ToolTurn
from octave.tools.errors import ToolError

__all__ = [
    "McpToolExecutor",
    "ToolError",
    "ToolExecutor",
    "ToolLoop",
    "ToolLoopLimitError",
    "ToolOutcome",
    "ToolTurn",
]
