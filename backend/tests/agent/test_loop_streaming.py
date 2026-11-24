"""ToolLoop streaming + sink emission (issue #45)."""

from collections.abc import AsyncIterator

from octave.agent.loop import ToolLoop
from octave.inference.adapter import InferenceAdapter
from octave.inference.config import AdapterConfig
from octave.inference.types import (
    CompletionChunk,
    CompletionRequest,
    CompletionResult,
    EmbeddingRequest,
    EmbeddingResult,
    Message,
    ModelInfo,
    ToolCallDelta,
)
from octave.tools.types import ProviderToolset, ToolRoute
from tests.agent.fakes import RecordingExecutor

USER = [Message(role="user", content="go")]
TOOLSET = ProviderToolset(
    tools=[], routes={"mcp__fs__read": ToolRoute(server_id="srv", tool_name="read")}
)


class ChunkAdapter(InferenceAdapter):
    """Yields queued chunk-script rounds; records requests."""

    def __init__(self, rounds: list[list[CompletionChunk]]) -> None:
        super().__init__(AdapterConfig(adapter="fake", base_url="http://fake.test/v1"))
        self._rounds = list(rounds)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResult:
        raise AssertionError("ToolLoop must not call complete() (issue #45)")

    async def stream(
        self, request: CompletionRequest
    ) -> AsyncIterator[CompletionChunk]:
        self.requests.append(request)
        for chunk in self._rounds.pop(0):
            yield chunk

    async def embed(self, request: EmbeddingRequest) -> EmbeddingResult:
        raise NotImplementedError

    async def list_models(self) -> list[ModelInfo]:
        return []


class RecordingSink:
    """Records sink calls as flat tuples."""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    async def on_thinking_delta(self, *, round_index: int, text: str) -> None:
        self.events.append(("thinking", round_index, text))

    async def on_text_delta(self, *, round_index: int, text: str) -> None:
        self.events.append(("text", round_index, text))

    async def on_tool_call_start(
        self, *, round_index: int, index: int, call_id: str, name: str
    ) -> None:
        self.events.append(("tool_start", round_index, index, call_id, name))

    async def on_tool_args_delta(
        self, *, round_index: int, index: int, arguments: str
    ) -> None:
        self.events.append(("tool_args", round_index, index, arguments))

    async def on_tool_result(
        self,
        *,
        round_index: int,
        call_id: str,
        name: str,
        content: str,
        is_error: bool,
    ) -> None:
        self.events.append(
            ("tool_result", round_index, call_id, name, content, is_error)
        )


async def test_assembles_result_from_chunks_and_emits_deltas() -> None:
    adapter = ChunkAdapter(
        [
            [
                CompletionChunk(delta_thinking="hmm", model="m"),
                CompletionChunk(delta_text="Hel", model="m"),
                CompletionChunk(delta_text="lo", model="m"),
                CompletionChunk(delta_text="", finish_reason="stop", model="m"),
            ]
        ]
    )
    sink = RecordingSink()
    turn = await ToolLoop(
        adapter=adapter, executor=RecordingExecutor(), stream_sink=sink
    ).run(list(USER), TOOLSET)
    assert turn.result.text == "Hello"
    assert turn.result.model == "m"
    assert turn.result.thinking == "hmm"
    assert sink.events == [
        ("thinking", 0, "hmm"),
        ("text", 0, "Hel"),
        ("text", 0, "lo"),
    ]


async def test_tool_round_emits_start_args_result() -> None:
    adapter = ChunkAdapter(
        [
            [
                CompletionChunk(
                    delta_tool_calls=[
                        ToolCallDelta(index=0, id="c1", name="mcp__fs__read")
                    ],
                    model="m",
                ),
                CompletionChunk(
                    delta_tool_calls=[
                        ToolCallDelta(index=0, arguments='{"p": 1}')
                    ],
                    model="m",
                ),
                CompletionChunk(delta_text="", finish_reason="tool_calls", model="m"),
            ],
            [CompletionChunk(delta_text="done", model="m")],
        ]
    )
    sink = RecordingSink()
    turn = await ToolLoop(
        adapter=adapter, executor=RecordingExecutor(), stream_sink=sink
    ).run(list(USER), TOOLSET)
    assert turn.result.text == "done"
    assert turn.tool_rounds == 1
    assert [e[0] for e in sink.events] == [
        "tool_start",
        "tool_args",
        "tool_result",
        "text",
    ]
    assert sink.events[0] == ("tool_start", 0, 0, "c1", "mcp__fs__read")
    assert sink.events[2] == ("tool_result", 0, "c1", "mcp__fs__read", "ok", False)


async def test_assembled_tool_call_matches_legacy_shape() -> None:
    adapter = ChunkAdapter(
        [
            [
                CompletionChunk(
                    delta_tool_calls=[
                        ToolCallDelta(index=0, id="c1", name="t", arguments='{"a')
                    ],
                    model="m",
                ),
                CompletionChunk(
                    delta_tool_calls=[ToolCallDelta(index=0, arguments='": 1}')],
                    model="m",
                ),
                CompletionChunk(delta_text="", finish_reason="tool_calls", model="m"),
            ],
            [CompletionChunk(delta_text="ok", model="m")],
        ]
    )
    turn = await ToolLoop(adapter=adapter, executor=RecordingExecutor()).run(
        list(USER), TOOLSET
    )
    # The history's assistant tool-call message must carry the assembled call.
    assistant = turn.messages[1]
    assert assistant.tool_calls is not None
    assert assistant.tool_calls[0].id == "c1"
    assert assistant.tool_calls[0].arguments == '{"a": 1}'


async def test_no_sink_is_silent() -> None:
    adapter = ChunkAdapter(
        [
            [
                CompletionChunk(delta_text="hi", model="m"),
                CompletionChunk(finish_reason="stop", model="m"),
            ]
        ]
    )
    turn = await ToolLoop(adapter=adapter, executor=RecordingExecutor()).run(
        list(USER), TOOLSET
    )
    assert turn.result.text == "hi"
