"""Agent plane: tool-use orchestration (issue #79) and instance lifecycle
(design spec #25).

Composition layer — the only package importing both octave.mcp and
octave.inference, plus octave.db for the instance manager. Future Agent
Manager components (router, result collector) land here as additional
modules; they do not exist yet.
"""

from octave.agent.errors import (
    AgentError,
    AgentNotFoundError,
    AgentPausedError,
    DeciderChoiceError,
    InstanceExistsError,
    InstanceNotFoundError,
    InvalidTransitionError,
    ModelBindingError,
    NotAMemberError,
    RoutingError,
    SessionNotFoundError,
    TerminalSessionError,
    ToolLoopLimitError,
    TurnInProgressError,
)
from octave.agent.executor import ToolExecutor
from octave.agent.instances import AgentInstanceManager, ResolvedModel, resolve_model
from octave.agent.loop import ToolLoop
from octave.agent.mcp_executor import McpToolExecutor
from octave.agent.registry import AgentRegistry, RunningAgent
from octave.agent.types import ToolOutcome, ToolTurn
from octave.tools.errors import ToolError

__all__ = [
    "AgentError",
    "AgentInstanceManager",
    "AgentNotFoundError",
    "AgentPausedError",
    "AgentRegistry",
    "DeciderChoiceError",
    "InstanceExistsError",
    "InstanceNotFoundError",
    "InvalidTransitionError",
    "McpToolExecutor",
    "ModelBindingError",
    "NotAMemberError",
    "ResolvedModel",
    "RoutingError",
    "RunningAgent",
    "SessionNotFoundError",
    "TerminalSessionError",
    "ToolError",
    "ToolExecutor",
    "ToolLoop",
    "ToolLoopLimitError",
    "ToolOutcome",
    "ToolTurn",
    "TurnInProgressError",
    "resolve_model",
]
