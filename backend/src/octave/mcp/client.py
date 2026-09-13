"""MCP client façade — the second of two modules importing ``mcp``.

Role (design spec quarantine rule): all MCP semantics live here — sessions,
requests, results, and the boundary where SDK exceptions are translated to
``octave.mcp.errors``. SDK types never appear in public signatures.
"""

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any

import anyio
from mcp import ClientSession
from mcp.shared.exceptions import McpError as SdkMcpError
from mcp.types import TextContent as SdkTextContent

from octave.mcp.config import McpSettings, ServerConfig
from octave.mcp.errors import (
    McpConnectionError,
    McpError,
    McpNotConnectedError,
    McpRpcError,
    McpTimeoutError,
)
from octave.mcp.transport import TransportStreams, open_transport
from octave.mcp.types import ServerInfo, ToolContent, ToolInfo, ToolResult

__all__ = ["McpClient"]

TransportFactory = Callable[
    [ServerConfig], AbstractAsyncContextManager[TransportStreams]
]
"""Seam letting tests inject in-memory streams instead of a real transport.
``open_transport`` itself satisfies this protocol (``@asynccontextmanager``)."""


class McpClient:
    """Typed façade over one MCP server connection.

    Safe for concurrent use within one event loop (same contract as
    ``InferenceAdapter``): request-ID correlation is the SDK session's job,
    so many ``call_tool``/``send_request`` calls may be in flight.
    """

    def __init__(
        self,
        *,
        transport_factory: TransportFactory = open_transport,
        settings: McpSettings | None = None,
    ) -> None:
        self._transport_factory = transport_factory
        self._settings = settings or McpSettings()
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._server_info: ServerInfo | None = None

    async def connect(self, config: ServerConfig) -> None:
        """Open transport + session and run the MCP initialize handshake."""
        if self._session is not None:
            raise McpError("Already connected — call aclose() first")
        stack = AsyncExitStack()
        try:
            streams = await stack.enter_async_context(self._transport_factory(config))
            session = await stack.enter_async_context(
                ClientSession(streams.read, streams.write)
            )
            with anyio.fail_after(self._settings.request_timeout_seconds):
                init = await session.initialize()
        except TimeoutError as exc:
            await stack.aclose()
            raise McpTimeoutError(
                f"initialize handshake timed out after "
                f"{self._settings.request_timeout_seconds}s"
            ) from exc
        except (FileNotFoundError, PermissionError) as exc:
            await stack.aclose()
            raise McpConnectionError(f"Could not spawn server process: {exc}") from exc
        except Exception:
            # Config, transport, or handshake failure — unwind the stack
            # before re-raising so a half-open connection never leaks.
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        self._server_info = ServerInfo(
            name=init.serverInfo.name,
            version=init.serverInfo.version,
            # The SDK types protocolVersion as ``str | int`` (legacy drafts
            # used integers); normalize to str for Octave's typed boundary.
            protocol_version=str(init.protocolVersion),
        )

    async def aclose(self) -> None:
        """Unwind the connection (terminate subprocess / close HTTP). Idempotent."""
        stack = self._stack
        self._stack = None
        self._session = None
        self._server_info = None
        if stack is not None:
            await stack.aclose()

    @property
    def server_info(self) -> ServerInfo:
        """Server identity captured from the initialize handshake."""
        if self._server_info is None:
            raise McpNotConnectedError("No server info — connect() first")
        return self._server_info

    async def ping(self) -> bool:
        """Round-trip liveness check."""
        session = self._require_session("ping")
        await self._run("ping", session.send_ping())
        return True

    async def list_tools(self) -> list[ToolInfo]:
        """Tools advertised by the server, mapped to Octave types."""
        session = self._require_session("list_tools")
        result = await self._run("list_tools", session.list_tools())
        return [
            ToolInfo(
                name=tool.name,
                description=tool.description,
                input_schema=dict(tool.inputSchema),
            )
            for tool in result.tools
        ]

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> ToolResult:
        """Invoke a tool. MCP-level tool failure comes back as
        ``ToolResult(is_error=True)``; a JSON-RPC error raises ``McpRpcError``.
        Non-text content blocks are skipped (deferred — see spec follow-ups)."""
        session = self._require_session("call_tool")
        result = await self._run(
            f"call_tool({name})", session.call_tool(name, arguments=arguments or {})
        )
        content: list[ToolContent] = []
        for block in result.content:
            if isinstance(block, SdkTextContent):
                content.append(ToolContent(kind="text", text=block.text))
        return ToolResult(content=content, is_error=bool(result.isError))

    def _require_session(self, operation: str) -> ClientSession:
        if self._session is None:
            raise McpNotConnectedError(f"cannot {operation}: not connected")
        return self._session

    async def _run(self, operation: str, awaitable: Awaitable[Any]) -> Any:
        """Await ``awaitable`` under the request timeout; translate failures.

        An SDK ``McpError`` (a JSON-RPC error response on the wire) becomes
        ``McpRpcError`` carrying the code verbatim; timeout becomes
        ``McpTimeoutError``. Callers only ever catch Octave types.
        """
        try:
            with anyio.fail_after(self._settings.request_timeout_seconds):
                return await awaitable
        except TimeoutError as exc:
            raise McpTimeoutError(
                f"{operation} timed out after {self._settings.request_timeout_seconds}s"
            ) from exc
        except SdkMcpError as exc:
            raise McpRpcError(
                exc.error.message, code=exc.error.code, data=exc.error.data
            ) from exc
