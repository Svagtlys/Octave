"""Ephemeral per-turn streaming seam (issue #45).

The sink receives deltas while a turn runs; nothing here is persisted.
Implementations must be cheap and failure-free from the loop's point of
view — the WS hub broadcast evicts dead sockets itself. ``round_index`` is
the 0-based completion round within one agent turn; ``index`` is the
tool-call index within the round (OpenAI streaming semantics)."""

from typing import Protocol

__all__ = ["StreamSink"]


class StreamSink(Protocol):
    """Delta feed for one agent turn. All methods are optional-by-absence:
    the loop passes None when nobody is listening."""

    async def on_thinking_delta(self, *, round_index: int, text: str) -> None: ...

    async def on_text_delta(self, *, round_index: int, text: str) -> None: ...

    async def on_tool_call_start(
        self, *, round_index: int, index: int, call_id: str, name: str
    ) -> None: ...

    async def on_tool_args_delta(
        self, *, round_index: int, index: int, arguments: str
    ) -> None: ...

    async def on_tool_result(
        self,
        *,
        round_index: int,
        call_id: str,
        name: str,
        content: str,
        is_error: bool,
    ) -> None: ...
