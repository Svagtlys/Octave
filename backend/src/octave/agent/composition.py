"""Composition root for the request flow (issue #60).

Pure wiring: callers resolve the seams (app.state in production, fakes in
tests) and own the session/transaction. Mirrors the ToolExecutor /
McpToolExecutor split: the router learns nothing about inference or MCP.
"""

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from octave.agent.decider import LlmTurnDecider
from octave.agent.errors import ModelBindingError
from octave.agent.instances import AgentInstanceManager
from octave.agent.registry import AgentRegistry
from octave.agent.router import MessageRouter
from octave.agent.streaming import StreamSink
from octave.agent.summaries import SessionSummarizer
from octave.agent.turn_runner import DefaultTurnRunner
from octave.db.adapter import DbAdapter
from octave.db.types import ExplicitModelBinding
from octave.inference.adapter import InferenceAdapter
from octave.mcp import ToolRegistry

__all__ = ["build_message_router", "build_session_summarizer"]


def build_message_router(
    session: AsyncSession,
    *,
    adapter: InferenceAdapter,
    tool_registry: ToolRegistry,
    adapter_name: str | None = None,
    tag_lookup: Callable[[str], str | None] | None = None,
    max_agent_turns: int = 4,
    stream_sink: StreamSink | None = None,
) -> MessageRouter:
    """Assemble decider + manager + registry + runner into the driver.
    The sink reaches the runner only — decider tokens never stream (#45)."""
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
            stream_sink=stream_sink,
        ),
        max_agent_turns=max_agent_turns,
    )


def build_session_summarizer(
    session: AsyncSession,
    *,
    db_adapter: DbAdapter,
    adapter: InferenceAdapter | None = None,
    adapter_name: str | None = None,
    tag_lookup: Callable[[str], str | None] | None = None,
) -> SessionSummarizer:
    """Wire the summarizer to the single configured inference adapter.

    ``adapter=None`` keeps the read paths (peek) usable when inference is
    unconfigured; ``adapter_for`` then fails loud on any generation
    attempt, mirroring DefaultTurnRunner's binding guard. The empty model
    string falls through to the adapter's own default at request time.
    """
    name = adapter_name or "openai"
    binding = ExplicitModelBinding(
        kind="explicit",
        adapter=name,
        model=(adapter.config.default_model if adapter else "") or "",
    )

    def adapter_for(requested: str | None) -> InferenceAdapter:
        if adapter is None or requested != name:
            raise ModelBindingError(
                f"summary binding requests adapter {requested!r} but only "
                f"{name!r} is configured"
            )
        return adapter

    return SessionSummarizer(
        session=session,
        db_adapter=db_adapter,
        binding=binding,
        adapter_for=adapter_for,
        tag_lookup=tag_lookup,
    )
