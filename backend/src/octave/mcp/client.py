"""MCP client façade — the second of two modules importing ``mcp``.

Role (design spec quarantine rule): all MCP semantics live here — sessions,
requests, results, and the boundary where SDK exceptions are translated to
``octave.mcp.errors``. SDK types never appear in public signatures.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any, cast

import anyio
from anyio.streams.memory import (
    MemoryObjectReceiveStream,
    MemoryObjectSendStream,
)
from mcp import ClientSession
from mcp.client.session import MessageHandlerFnT
from mcp.shared.exceptions import McpError as SdkMcpError
from mcp.types import ClientNotification, ClientRequest
from mcp.types import TextContent as SdkTextContent
from pydantic import BaseModel, ConfigDict

from octave.mcp.config import McpSettings, ServerConfig
from octave.mcp.errors import (
    McpConnectionError,
    McpError,
    McpNotConnectedError,
    McpRpcError,
    McpTimeoutError,
)
from octave.mcp.transport import TransportStreams, open_transport
from octave.mcp.types import (
    Notification,
    ServerInfo,
    ToolContent,
    ToolInfo,
    ToolResult,
)

__all__ = ["McpClient"]

TransportFactory = Callable[
    [ServerConfig], AbstractAsyncContextManager[TransportStreams]
]
"""Seam letting tests inject in-memory streams instead of a real transport.
``open_transport`` itself satisfies this protocol (``@asynccontextmanager``)."""


class _RawOutboundMessage(BaseModel):
    """Permissive request/notification envelope for the raw escape hatch.

    The SDK session serializes outbound messages with
    ``model_dump(by_alias=True, mode="json", exclude_none=True)`` — this
    shape dumps to exactly ``{"method": …, "params": …}`` and carries any
    method name, bypassing the SDK's typed union without touching its wire
    behavior (ID correlation, error raising stay the session's job).
    """

    model_config = ConfigDict(extra="allow")

    method: str
    params: dict[str, Any] | None = None


class _RawResultModel(BaseModel):
    """Accepts any result payload; the escape hatch returns it as a dict."""

    model_config = ConfigDict(extra="allow")


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
        self._notification_senders: list[MemoryObjectSendStream[Notification]] = []

    async def connect(self, config: ServerConfig) -> None:
        """Open transport + session and run the MCP initialize handshake."""
        if self._session is not None:
            raise McpError("Already connected — call aclose() first")
        stack = AsyncExitStack()
        try:
            streams = await stack.enter_async_context(self._transport_factory(config))
            session = await stack.enter_async_context(
                ClientSession(
                    streams.read,
                    streams.write,
                    # ``*args`` absorbs both SDK handler conventions, so it
                    # can't structurally match the two-arg Protocol — cast.
                    message_handler=cast(MessageHandlerFnT, self._on_inbound_message),
                )
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

    async def send_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Raw JSON-RPC escape hatch: any method, raw dict result.

        Covers methods the façade hasn't typed yet without leaking SDK types.
        """
        session = self._require_session(f"send_request({method})")
        result = await self._run(
            method,
            session.send_request(
                cast(ClientRequest, _RawOutboundMessage(method=method, params=params)),
                cast("type[Any]", _RawResultModel),
            ),
        )
        return dict(result.model_dump())

    async def send_notification(
        self, method: str, params: dict[str, Any] | None = None
    ) -> None:
        """Raw JSON-RPC notification (fire-and-forget)."""
        session = self._require_session(f"send_notification({method})")
        message = _RawOutboundMessage(method=method, params=params)
        await self._run(
            method,
            session.send_notification(cast(ClientNotification, message)),
        )

    def subscribe_notifications(self) -> AsyncIterator[Notification]:
        """A private stream of inbound server→client notifications.

        Registration happens at call time (not first ``__anext__``) so a
        notification sent immediately after subscribing is buffered, not
        dropped. Close the returned iterator when done.
        """
        send, receive = anyio.create_memory_object_stream[Notification](
            max_buffer_size=100
        )
        self._notification_senders.append(send)
        return self._drain_notifications(receive, send)

    async def _drain_notifications(
        self,
        receive: MemoryObjectReceiveStream[Notification],
        send: MemoryObjectSendStream[Notification],
    ) -> AsyncIterator[Notification]:
        try:
            async for notification in receive:
                yield notification
        finally:
            if send in self._notification_senders:
                self._notification_senders.remove(send)
            await send.aclose()
            await receive.aclose()

    async def _on_inbound_message(self, *args: Any) -> None:
        """``ClientSession`` message_handler: fan notifications to subscribers.

        ``*args`` absorbs both SDK call conventions — the current
        ``(context, message)`` and the older ``(message)`` — the message is
        always the last positional argument. Inbound server→client *requests*
        never reach here answered: the SDK answers them itself unless a
        callback is registered — the seam where a request-handler registry
        plugs in later (design decision 4).
        """
        message = args[-1]
        root = getattr(message, "root", message)
        method = getattr(root, "method", None)
        if not isinstance(method, str):
            return  # not a notification — nothing to fan out
        raw_params = getattr(root, "params", None)
        if raw_params is not None and hasattr(raw_params, "model_dump"):
            params = dict(
                raw_params.model_dump(
                    by_alias=True, mode="json", exclude_none=True
                )
            )
        else:
            params = dict(getattr(raw_params, "root", raw_params) or {})
        for send in list(self._notification_senders):
            try:
                send.send_nowait(Notification(method=method, params=params))
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                # Subscriber went away; drop it silently.
                self._notification_senders.remove(send)
            except anyio.WouldBlock:
                pass  # subscriber buffer full — drop the notification

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
