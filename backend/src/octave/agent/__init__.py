"""Agent plane: tool-use orchestration (issue #79) and instance lifecycle
(design spec #25).

Composition layer — the only package importing both octave.mcp and
octave.inference, plus octave.db for the instance manager. Manager
components ship here as additional modules: instance lifecycle
(#25), registry (#26), message routing (#27). Result collection (#4) and
scheduling (#6) remain.
"""

from octave.agent.decider import (
    Candidate,
    Decision,
    DecisionState,
    LlmTurnDecider,
    TurnDecider,
)
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
from octave.agent.router import (
    MessageRouter,
    RouteOutcome,
    StopReason,
    TurnRecord,
    TurnRunner,
)
from octave.agent.types import ToolOutcome, ToolTurn
from octave.tools.errors import ToolError

__all__ = [
    "AgentError",
    "AgentInstanceManager",
    "AgentNotFoundError",
    "AgentPausedError",
    "AgentRegistry",
    "Candidate",
    "DeciderChoiceError",
    "Decision",
    "DecisionState",
    "InstanceExistsError",
    "InstanceNotFoundError",
    "InvalidTransitionError",
    "LlmTurnDecider",
    "McpToolExecutor",
    "MessageRouter",
    "ModelBindingError",
    "NotAMemberError",
    "ResolvedModel",
    "RouteOutcome",
    "RoutingError",
    "RunningAgent",
    "SessionNotFoundError",
    "StopReason",
    "TerminalSessionError",
    "ToolError",
    "ToolExecutor",
    "ToolLoop",
    "ToolLoopLimitError",
    "ToolOutcome",
    "ToolTurn",
    "TurnDecider",
    "TurnInProgressError",
    "TurnRecord",
    "TurnRunner",
    "resolve_model",
]
