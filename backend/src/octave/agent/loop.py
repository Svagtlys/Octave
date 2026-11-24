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
from octave.agent.streaming import StreamSink
from octave.agent.types import ToolOutcome, ToolTurn
from octave.inference.adapter import InferenceAdapter
from octave.inference.types import (
    CompletionRequest,
    CompletionResult,
    Message,
    ToolCall,
)
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
        stream_sink: StreamSink | None = None,
    ) -> None:
        self._adapter = adapter
        self._executor = executor
        self._max_tool_rounds = max_tool_rounds
        self._stream_sink = stream_sink

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
            result = await self._stream_round(
                CompletionRequest(model=model, messages=history, tools=tools),
                round_index=rounds,
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
            logger.debug(
                "tool round | round=%s calls=%s", rounds, len(result.tool_calls)
            )
            for call in result.tool_calls:
                history.append(
                    await self._execute(call, toolset, round_index=rounds - 1)
                )

    async def _stream_round(
        self, request: CompletionRequest, *, round_index: int
    ) -> CompletionResult:
        """Stream one completion and assemble the final result. Assembled
        fields equal what complete() would have returned (spec Decision 2):
        what streams is exactly what commits."""
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        model = ""
        finish: str | None = None
        fragments: dict[int, dict[str, str]] = {}
        async for chunk in self._adapter.stream(request):
            if chunk.model:
                model = chunk.model
            if chunk.delta_text:
                text_parts.append(chunk.delta_text)
                if self._stream_sink is not None:
                    await self._stream_sink.on_text_delta(
                        round_index=round_index, text=chunk.delta_text
                    )
            if chunk.delta_thinking:
                thinking_parts.append(chunk.delta_thinking)
                if self._stream_sink is not None:
                    await self._stream_sink.on_thinking_delta(
                        round_index=round_index, text=chunk.delta_thinking
                    )
            for delta in chunk.delta_tool_calls:
                buffer = fragments.setdefault(
                    delta.index, {"id": "", "name": "", "arguments": ""}
                )
                if delta.id and not buffer["id"]:
                    buffer["id"] = delta.id
                    buffer["name"] = delta.name or ""
                    if self._stream_sink is not None:
                        await self._stream_sink.on_tool_call_start(
                            round_index=round_index,
                            index=delta.index,
                            call_id=delta.id,
                            name=buffer["name"],
                        )
                elif delta.name and not buffer["name"]:
                    buffer["name"] = delta.name
                if delta.arguments:
                    buffer["arguments"] += delta.arguments
                    if self._stream_sink is not None:
                        await self._stream_sink.on_tool_args_delta(
                            round_index=round_index,
                            index=delta.index,
                            arguments=delta.arguments,
                        )
            if chunk.finish_reason:
                finish = chunk.finish_reason
        tool_calls = [
            ToolCall(
                id=fragments[index]["id"],
                name=fragments[index]["name"],
                arguments=fragments[index]["arguments"],
            )
            for index in sorted(fragments)
        ]
        return CompletionResult(
            text="".join(text_parts),
            model=model,
            finish_reason=finish,
            tool_calls=tool_calls or None,
            thinking="".join(thinking_parts) or None,
        )

    async def _emit_tool_result(
        self, call: ToolCall, round_index: int, outcome: ToolOutcome
    ) -> None:
        if self._stream_sink is not None:
            await self._stream_sink.on_tool_result(
                round_index=round_index,
                call_id=call.id,
                name=call.name,
                content=outcome.content,
                is_error=outcome.is_error,
            )

    async def _execute(
        self, call: ToolCall, toolset: ProviderToolset, *, round_index: int
    ) -> Message:
        """One call -> one tool message; Tier-1 failures never raise."""
        route = toolset.routes.get(call.name)
        if route is None:
            outcome = ToolOutcome(
                content=f"Unknown tool: {call.name}", is_error=True
            )
            await self._emit_tool_result(call, round_index, outcome)
            return self._tool_message(call, outcome)
        arguments = self._parse_arguments(call)
        if isinstance(arguments, str):  # parse error message
            outcome = ToolOutcome(
                content=(
                    "Tool call arguments are not a valid JSON object: "
                    f"{arguments}"
                ),
                is_error=True,
            )
            await self._emit_tool_result(call, round_index, outcome)
            return self._tool_message(call, outcome)
        outcome = await self._executor.call(route.server_id, route.tool_name, arguments)
        if outcome.is_error:
            logger.warning(
                "tool call failed | tool=%s server=%s error=%s",
                call.name,
                route.server_id,
                outcome.content,
            )
        await self._emit_tool_result(call, round_index, outcome)
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
