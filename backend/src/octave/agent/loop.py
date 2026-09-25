"""The reason → act → observe loop (issue #79).

Composes an InferenceAdapter, a ToolExecutor, and a pre-built
ProviderToolset (#78 routes). Tier-1 tool failures become error tool
messages the model can react to; the round limit and adapter failures
raise (spec decision 3).
"""

import json
import logging
from typing import Any

from octave.agent.errors import ToolLoopLimitError
from octave.agent.executor import ToolExecutor
from octave.agent.types import ToolOutcome, ToolTurn
from octave.inference.adapter import InferenceAdapter
from octave.inference.types import CompletionRequest, Message, ToolCall
from octave.tools.types import ProviderToolset

__all__ = ["ToolLoop"]

logger = logging.getLogger(__name__)

_NO_OUTPUT = "(no output)"


class ToolLoop:
    """Runs one orchestrated turn: completions, tool rounds, final synthesis."""

    def __init__(
        self,
        *,
        adapter: InferenceAdapter,
        executor: ToolExecutor,
        max_tool_rounds: int = 8,
    ) -> None:
        self._adapter = adapter
        self._executor = executor
        self._max_tool_rounds = max_tool_rounds

    async def run(
        self,
        messages: list[Message],
        toolset: ProviderToolset,
        *,
        model: str | None = None,
    ) -> ToolTurn:
        """Drive messages to a final (no tool_calls) completion.

        Raises ToolLoopLimitError if the model still requests tools after
        ``max_tool_rounds`` executions; AdapterError propagates untouched.
        """
        history = list(messages)
        rounds = 0
        tools = toolset.tools or None
        while True:
            result = await self._adapter.complete(
                CompletionRequest(model=model, messages=history, tools=tools)
            )
            if not result.tool_calls:
                history.append(Message(role="assistant", content=result.text))
                return ToolTurn(messages=history, result=result, tool_rounds=rounds)
            history.append(
                Message(
                    role="assistant", content=result.text, tool_calls=result.tool_calls
                )
            )
            if rounds >= self._max_tool_rounds:
                logger.warning(
                    "tool loop limit reached | max_tool_rounds=%s calls=%s",
                    self._max_tool_rounds,
                    len(result.tool_calls),
                )
                raise ToolLoopLimitError(
                    f"tool loop exhausted after {self._max_tool_rounds} rounds",
                    messages=history,
                )
            rounds += 1
            logger.debug("tool round | round=%s calls=%s", rounds, len(result.tool_calls))
            for call in result.tool_calls:
                history.append(await self._execute(call, toolset))

    async def _execute(self, call: ToolCall, toolset: ProviderToolset) -> Message:
        """One call -> one tool message; Tier-1 failures never raise."""
        route = toolset.routes.get(call.name)
        if route is None:
            return self._tool_message(
                call, ToolOutcome(content=f"Unknown tool: {call.name}", is_error=True)
            )
        arguments = self._parse_arguments(call)
        if isinstance(arguments, str):  # parse error message
            return self._tool_message(
                call,
                ToolOutcome(
                    content=f"Tool call arguments are not a valid JSON object: {arguments}",
                    is_error=True,
                ),
            )
        outcome = await self._executor.call(route.server_id, route.tool_name, arguments)
        if outcome.is_error:
            logger.warning(
                "tool call failed | tool=%s server=%s error=%s",
                call.name,
                route.server_id,
                outcome.content,
            )
        return self._tool_message(call, outcome)

    @staticmethod
    def _parse_arguments(call: ToolCall) -> dict[str, Any] | str:
        """Parsed dict, or an error description string."""
        try:
            parsed = json.loads(call.arguments or "{}")
        except json.JSONDecodeError as exc:
            return str(exc)
        if not isinstance(parsed, dict):
            return f"expected an object, got {type(parsed).__name__}"
        return parsed

    @staticmethod
    def _tool_message(call: ToolCall, outcome: ToolOutcome) -> Message:
        content = outcome.content or _NO_OUTPUT
        if outcome.is_error:
            content = f"Error: {content}"
        return Message(
            role="tool", content=content, tool_call_id=call.id, name=call.name
        )
