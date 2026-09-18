"""In-process harness: an SDK ``Server`` wired to ``McpClient`` via memory streams.

The fixture ``harness`` yields ``(client, peer)`` via its context manager.
``client`` is connected (initialize handshake done); ``peer`` pokes the wire
directly — sending raw notifications/requests and reading raw responses — for
tests that need server-initiated traffic the harness ``Server`` cannot
produce on its own.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any

import anyio
import pytest
from anyio.streams.memory import (
    MemoryObjectReceiveStream,
    MemoryObjectSendStream,
)
from mcp.server import Server
from mcp.shared.exceptions import McpError as SdkMcpError
from mcp.shared.memory import create_client_server_memory_streams
from mcp.shared.message import SessionMessage
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ErrorData,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    ServerResult,
    TextContent,
    Tool,
)

from octave.mcp.client import McpClient
from octave.mcp.config import McpSettings, ServerConfig, StdioConfig
from octave.mcp.transport import TransportStreams

# The harness server's fixed toolset; every client test exercises these.
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
}
_TOOL_NAMES = ("echo", "slow", "tool_error")


def build_server() -> Server:
    """Low-level SDK server with a deterministic toolset.

    - ``echo``       → returns ``arguments["text"]``
    - ``slow``       → sleeps 30 s (client timeout tests)
    - ``tool_error`` → MCP-level failure: CallToolResult with ``isError``
    - unknown name   → raises SDK McpError(-32601) → JSON-RPC error response
    """
    server = Server("harness")

    @server.list_tools()
    async def _list() -> list[Tool]:
        return [
            Tool(name=n, description=n, inputSchema=dict(_SCHEMA)) for n in _TOOL_NAMES
        ]

    # Registered as a raw request handler rather than via @server.call_tool():
    # SDK 1.30's decorator swallows exceptions raised by the handler into an
    # isError=True CallToolResult (lowlevel/server.py:589), so an SdkMcpError
    # raised inside a decorated handler never reaches the dispatcher that
    # converts it to a JSON-RPC error response (lowlevel/server.py:777).
    async def _raw_call_tool(req: CallToolRequest) -> ServerResult:
        name = req.params.name
        arguments = req.params.arguments or {}
        if name == "echo":
            text = str(arguments.get("text", ""))
            content = [TextContent(type="text", text=text)]
            return ServerResult(CallToolResult(content=content))
        if name == "slow":
            await anyio.sleep(30)
            return ServerResult(
                CallToolResult(content=[TextContent(type="text", text="too late")])
            )
        if name == "tool_error":
            return ServerResult(
                CallToolResult(
                    content=[TextContent(type="text", text="tool failed")],
                    isError=True,
                )
            )
        raise SdkMcpError(ErrorData(code=-32601, message=f"unknown tool: {name}"))

    server.request_handlers[CallToolRequest] = _raw_call_tool

    return server


@dataclass
class Peer:
    """Direct wire access to the server side of the memory streams."""

    sread: MemoryObjectReceiveStream[SessionMessage | Exception]
    swrite: MemoryObjectSendStream[SessionMessage]

    async def send_notification(self, method: str, params: dict[str, Any]) -> None:
        """Push a raw server→client JSON-RPC notification."""
        await self.swrite.send(
            SessionMessage(
                JSONRPCMessage(
                    root=JSONRPCNotification(
                        jsonrpc="2.0", method=method, params=params
                    )
                )
            )
        )

    async def send_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        """Push a raw server→client JSON-RPC request (inbound-request seam)."""
        await self.swrite.send(
            SessionMessage(
                JSONRPCMessage(
                    root=JSONRPCRequest(
                        jsonrpc="2.0", method=method, params=params or {}, id=42
                    )
                )
            )
        )

    async def next_message(self) -> JSONRPCMessage:
        """Read the next message the client sent (e.g. an error response)."""
        message = await self.sread.receive()
        assert isinstance(message, SessionMessage)
        return message.message


@asynccontextmanager
async def harness_cm(
    *,
    request_timeout: float = 5.0,
    on_lost: Callable[[str], None] | None = None,
) -> AsyncIterator[tuple[McpClient, Peer]]:
    """Connect an ``McpClient`` to an in-process SDK server."""
    server = build_server()
    async with create_client_server_memory_streams() as (
        (cread, cwrite),
        (sread, swrite),
    ):

        @asynccontextmanager
        async def _factory(_config: ServerConfig) -> AsyncIterator[TransportStreams]:
            # The config is ignored: we hand the client pre-built streams.
            yield TransportStreams(read=cread, write=cwrite)

        client = McpClient(
            transport_factory=_factory,
            settings=McpSettings(request_timeout_seconds=request_timeout),
            on_lost=on_lost,
        )
        async with anyio.create_task_group() as tg:

            async def _run_server() -> None:
                await server.run(sread, swrite, server.create_initialization_options())

            tg.start_soon(_run_server)
            await client.connect(StdioConfig(command="in-process"))
            try:
                yield client, Peer(sread=sread, swrite=swrite)
            finally:
                await client.aclose()


@pytest.fixture
def harness() -> Callable[..., AbstractAsyncContextManager[tuple[McpClient, Peer]]]:
    """Return the harness cm: ``async with harness() as (client, peer)``."""
    return harness_cm
