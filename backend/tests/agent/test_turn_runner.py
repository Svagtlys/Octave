"""DefaultTurnRunner (issue #60, Integration #1).

Real SQLite (session_factory) + scripted adapter (tests/agent/fakes.py)
+ duck-typed fake registry. Asserts the transcript bracket the router
expects: context_injection once, tool_call/tool_result per executed call,
final text returned. Binding adapter mismatch fails loud.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.agent import AgentInstanceManager, AgentRegistry, ModelBindingError
from octave.agent.turn_runner import DefaultTurnRunner
from octave.db.event_store import EventStore
from octave.db.models import Agent, Session, User
from octave.inference.types import Message
from octave.mcp import ServerToolInventory, ToolContent, ToolInfo, ToolResult
from tests.agent.fakes import ScriptedAdapter, final_result, tool_call, tool_result

_FAKE_BINDING = {"kind": "explicit", "adapter": "fake", "model": "mock-model"}
_OTHER_BINDING = {"kind": "explicit", "adapter": "other", "model": "x"}


class FakeRegistry:
    """Duck-typed ToolRegistry: inventory() + call_tool()."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []

    async def inventory(self) -> list[ServerToolInventory]:
        return [
            ServerToolInventory(
                server_id="fs",
                server_name="fs",
                state="connected",
                tools=[ToolInfo(name="write", input_schema={"type": "object"})],
            )
        ]

    async def call_tool(
        self, id: str, name: str, arguments: dict | None = None
    ) -> ToolResult:
        self.calls.append((id, name, arguments or {}))
        return ToolResult(content=[ToolContent(kind="text", text="wrote it")])


async def _seed(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    binding: dict | None = _FAKE_BINDING,
) -> str:
    async with session_factory() as session:
        session.add(User(id="u_1", username="alice", display_name="Alice"))
        session.add(Agent(id="a_1", name="Octave", model_binding=binding))
        session.add(Session(id="s_1", created_by_user_id="u_1", status="active"))
        await session.commit()
    async with session_factory() as session:
        instance = await AgentInstanceManager(session).spawn(
            agent_id="a_1", session_id="s_1"
        )
        await session.commit()
        return instance.id


async def _run(session_factory, adapter, registry, instance_id):
    async with session_factory() as session:
        running = await AgentRegistry(session).get_instance(instance_id)
        runner = DefaultTurnRunner(
            session, adapter=adapter, registry=registry, adapter_name="fake"
        )
        reply = await runner.run_turn(
            instance=running, messages=[Message(role="user", content="hi")]
        )
        await session.commit()
        return reply.text


async def _kinds(session_factory) -> list[tuple[str, dict]]:
    async with session_factory() as session:
        events = await EventStore(session).read("s_1")
    return [(e.kind, e.payload) for e in events]


async def test_plain_reply_injects_context_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _seed(session_factory)
    adapter = ScriptedAdapter([final_result("Hello!")])
    text = await _run(session_factory, adapter, FakeRegistry(), instance_id)
    assert text == "Hello!"
    events = await _kinds(session_factory)
    assert [k for k, _ in events] == ["context_injection"]
    # Second turn: injection short-circuits (idempotent session-start).
    await _run(
        session_factory,
        ScriptedAdapter([final_result("again")]),
        FakeRegistry(),
        instance_id,
    )
    events = await _kinds(session_factory)
    assert [k for k, _ in events] == ["context_injection"]


async def test_tool_round_recorded_in_bracket(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _seed(session_factory)
    adapter = ScriptedAdapter(
        [
            tool_result(tool_call("c1", "mcp__fs__write", '{"path": "a.txt"}')),
            final_result("Created a.txt"),
        ]
    )
    registry = FakeRegistry()
    text = await _run(session_factory, adapter, registry, instance_id)
    assert text == "Created a.txt"
    assert registry.calls == [("fs", "write", {"path": "a.txt"})]
    events = await _kinds(session_factory)
    assert [k for k, _ in events] == [
        "context_injection",
        "tool_call",
        "tool_result",
    ]
    call_payload = events[1][1]
    assert call_payload["tool"] == "mcp__fs__write"
    assert call_payload["arguments"] == '{"path": "a.txt"}'
    result_payload = events[2][1]
    assert result_payload["tool"] == "mcp__fs__write"
    assert result_payload["content"] == "wrote it"
    assert result_payload["is_error"] is False


async def test_error_tool_result_flagged(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _seed(session_factory)

    class FailingRegistry(FakeRegistry):
        async def call_tool(self, id, name, arguments=None):
            return ToolResult(
                content=[ToolContent(kind="text", text="boom")], is_error=True
            )

    adapter = ScriptedAdapter(
        [tool_result(tool_call("c1", "mcp__fs__write")), final_result("failed sorry")]
    )
    await _run(session_factory, adapter, FailingRegistry(), instance_id)
    events = await _kinds(session_factory)
    result = next(p for k, p in events if k == "tool_result")
    assert result["is_error"] is True
    assert result["content"] == "Error: boom"  # the loop's Tier-1 prefix


async def test_binding_adapter_mismatch_fails_loud(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    instance_id = await _seed(session_factory, binding=_OTHER_BINDING)
    with pytest.raises(ModelBindingError):
        await _run(
            session_factory,
            ScriptedAdapter([final_result("x")]),
            FakeRegistry(),
            instance_id,
        )


async def test_missing_binding_fails_loud(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    # The spawn-time gate (_validate_binding in instances.py) rejects a
    # NULL binding before any turn runs — the agent is unusable by design,
    # and the runner's resolve step can never see it.
    with pytest.raises(ModelBindingError):
        await _seed(session_factory, binding=None)
