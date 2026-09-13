"""Transport constructors — one of exactly two modules importing ``mcp``.

Bounded role (design spec quarantine rule): config → SDK transport selection
and context-manager plumbing only. No sessions, no MCP semantics. The
returned streams are consumed by ``octave.mcp.client``.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from anyio.streams.memory import (
    MemoryObjectReceiveStream,
    MemoryObjectSendStream,
)
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.message import SessionMessage

from octave.mcp.config import HttpConfig, ServerConfig, StdioConfig
from octave.mcp.errors import McpConfigError

__all__ = ["TransportStreams", "open_transport"]


@dataclass(frozen=True)
class TransportStreams:
    """The read/write message streams of an open transport."""

    read: MemoryObjectReceiveStream[SessionMessage | Exception]
    write: MemoryObjectSendStream[SessionMessage]


@asynccontextmanager
async def open_transport(config: ServerConfig) -> AsyncIterator[TransportStreams]:
    """Open the SDK transport matching ``config``; teardown runs on exit.

    ``StdioConfig`` spawns the subprocess (SDK ``stdio_client``);
    ``HttpConfig`` opens a Streamable HTTP session (SDK
    ``streamablehttp_client``; the session-id callback is deliberately
    ignored). Malformed or unknown configs raise ``McpConfigError`` before
    any I/O.
    """
    if isinstance(config, StdioConfig):
        if not config.command:
            raise McpConfigError("StdioConfig.command must be a non-empty string")
        params = StdioServerParameters(
            command=config.command,
            args=list(config.args),
            # Empty env must fall back to the SDK's default environment
            # (PATH etc.), not replace it with nothing.
            env=dict(config.env) or None,
        )
        async with stdio_client(params) as (read, write):
            yield TransportStreams(read=read, write=write)
    elif isinstance(config, HttpConfig):
        if not config.url.startswith(("http://", "https://")):
            raise McpConfigError(
                f"Invalid HttpConfig.url {config.url!r}: needs http(s):// scheme"
            )
        async with streamablehttp_client(config.url, headers=dict(config.headers)) as (
            read,
            write,
            _get_session_id,
        ):
            yield TransportStreams(read=read, write=write)
    else:  # pragma: no cover - guards config types added in the future
        raise McpConfigError(f"Unknown ServerConfig type: {type(config).__name__}")
