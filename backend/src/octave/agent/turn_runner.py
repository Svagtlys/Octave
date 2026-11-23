"""Production TurnRunner (issue #60, Integration #1).

Composes the shipped stages inside one claimed turn: idempotent context
injection (CM #34) → model-binding resolution → ToolLoop over
McpToolExecutor → tool-call/tool-result transcript bracket. The router
records the assistant_message itself; the final loop message is skipped.
Never commits (router convention). is_error is read back from the loop's
"Error: " Tier-1 prefix — the only external signal ToolLoop exposes.
"""

import logging
from collections.abc import Callable, Sequence

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from octave.agent.errors import AgentNotFoundError, ModelBindingError
from octave.agent.instances import resolve_model
from octave.agent.loop import ToolLoop
from octave.agent.mcp_executor import McpToolExecutor
from octave.agent.registry import RunningAgent
from octave.context import ContextInjector
from octave.db.event_store import EventStore
from octave.db.models import Agent, Participant
from octave.db.types import EventKind, ModelBinding
from octave.inference.adapter import InferenceAdapter
from octave.inference.types import Message
from octave.mcp import ToolRegistry
from octave.tools import translate_tools

__all__ = ["DefaultTurnRunner"]

logger = logging.getLogger(__name__)

_BINDING_ADAPTER: TypeAdapter[ModelBinding] = TypeAdapter(ModelBinding)

_TOOL_ERROR_PREFIX = "Error: "
"""ToolLoop._tool_message prefixes Tier-1 failures with this; the transcript
records the flag so readers don't re-parse prose."""


class DefaultTurnRunner:
    """TurnRunner port implementation: context assembly + tool loop."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        adapter: InferenceAdapter,
        registry: ToolRegistry,
        adapter_name: str | None = None,
        tag_lookup: Callable[[str], str | None] | None = None,
    ) -> None:
        self._session = session
        self._adapter = adapter
        self._registry = registry
        self._adapter_name = adapter_name
        self._tag_lookup = tag_lookup
        self._events = EventStore(session)

    async def run_turn(
        self, *, instance: RunningAgent, messages: list[Message]
    ) -> str:
        await ContextInjector(self._session).ensure_injected(
            session_id=instance.session_id, agent_id=instance.agent_id
        )
        model = await self._resolve_model(instance.agent_id)
        toolset = translate_tools(await self._registry.inventory())
        loop = ToolLoop(
            adapter=self._adapter, executor=McpToolExecutor(self._registry)
        )
        turn = await loop.run(list(messages), toolset, model=model)
        await self._record_bracket(instance, turn.messages[len(messages) :])
        return turn.result.text

    async def _resolve_model(self, agent_id: str) -> str:
        agent = await self._session.get(Agent, agent_id)
        if agent is None:  # RESTRICT makes this unreachable; defensive
            raise AgentNotFoundError(agent_id)
        try:
            binding = _BINDING_ADAPTER.validate_python(agent.model_binding)
        except ValidationError as exc:
            raise ModelBindingError(
                f"agent {agent_id} has invalid model_binding: {exc}"
            ) from exc
        resolved = resolve_model(binding, tag_lookup=self._tag_lookup)
        if (
            resolved.adapter is not None
            and self._adapter_name is not None
            and resolved.adapter != self._adapter_name
        ):
            raise ModelBindingError(
                f"agent {agent_id} binds adapter {resolved.adapter!r} but the "
                f"configured adapter is {self._adapter_name!r}"
            )
        return resolved.model

    async def _record_bracket(
        self, instance: RunningAgent, appended: Sequence[Message]
    ) -> None:
        """Record tool events in the turn bracket. The final assistant
        message (no tool_calls) is skipped — the router records it."""
        author = await self._session.scalar(
            select(Participant.id).where(Participant.agent_id == instance.agent_id)
        )
        for message in appended:
            if message.tool_calls:
                for call in message.tool_calls:
                    await self._events.append(
                        instance.session_id,
                        EventKind.TOOL_CALL,
                        author_participant_id=author,
                        payload={
                            "tool": call.name,
                            "arguments": call.arguments or "{}",
                        },
                    )
            elif message.role == "tool":
                content = message.content or ""
                await self._events.append(
                    instance.session_id,
                    EventKind.TOOL_RESULT,
                    author_participant_id=author,
                    payload={
                        "tool": message.name or "",
                        "content": content,
                        "is_error": content.startswith(_TOOL_ERROR_PREFIX),
                    },
                )
