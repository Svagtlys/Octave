"""Composition root for the request flow (issue #60).

Pure wiring: callers resolve the seams (app.state in production, fakes in
tests) and own the session/transaction. Mirrors the ToolExecutor /
McpToolExecutor split: the router learns nothing about inference or MCP.
"""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from octave.agent.decider import LlmTurnDecider
from octave.agent.instances import AgentInstanceManager
from octave.agent.registry import AgentRegistry
from octave.agent.router import MessageRouter
from octave.agent.turn_runner import DefaultTurnRunner
from octave.inference.adapter import InferenceAdapter
from octave.mcp import ToolRegistry

__all__ = ["build_message_router"]


def build_message_router(
    session: AsyncSession,
    *,
    adapter: InferenceAdapter,
    tool_registry: ToolRegistry,
    adapter_name: str | None = None,
    tag_lookup: Callable[[str], str | None] | None = None,
    max_agent_turns: int = 4,
) -> MessageRouter:
    """Assemble decider + manager + registry + runner into the driver."""
    return MessageRouter(
        session=session,
        manager=AgentInstanceManager(session),
        registry=AgentRegistry(session),
        decider=LlmTurnDecider(adapter=adapter),
        turn_runner=DefaultTurnRunner(
            session,
            adapter=adapter,
            registry=tool_registry,
            adapter_name=adapter_name,
            tag_lookup=tag_lookup,
        ),
        max_agent_turns=max_agent_turns,
    )
