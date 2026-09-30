"""Agent plane: tool-use orchestration (issue #79) and instance lifecycle
(design spec #25).

Composition layer — the only package importing both octave.mcp and
octave.inference, plus octave.db for the instance manager. Manager
components ship here as additional modules: instance lifecycle
(#25), registry (#26), message routing (#27). Scheduling (#6) remains.
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
    SummaryError,
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
from octave.agent.summaries import (
    HeadTailDigest,
    SessionSummarizer,
    SessionSummary,
    SummaryContext,
    TranscriptDigest,
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
    "HeadTailDigest",
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
    "SessionSummarizer",
    "SessionSummary",
    "StopReason",
    "SummaryContext",
    "SummaryError",
    "TerminalSessionError",
    "ToolError",
    "ToolExecutor",
    "ToolLoop",
    "ToolLoopLimitError",
    "ToolOutcome",
    "ToolTurn",
    "TranscriptDigest",
    "TurnDecider",
    "TurnInProgressError",
    "TurnRecord",
    "TurnRunner",
    "resolve_model",
]
