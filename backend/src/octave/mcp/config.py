"""MCP server configuration.

Two layers, mirroring ``octave.inference.config``: the frozen ``StdioConfig`` /
``HttpConfig`` dataclasses are what the façade consumes — populated from the
unified database at the composition root later (roadmap #7), from tests today.
``McpSettings`` carries global env-backed defaults (``OCTAVE_MCP_*``).

``env`` values and ``headers`` may carry secrets: never log them — ``__repr__``
redacts every value to ``***``.
"""

from dataclasses import dataclass, field

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["HttpConfig", "McpSettings", "ServerConfig", "StdioConfig"]


def _redact(mapping: dict[str, str]) -> dict[str, str]:
    """Replace every value with ``***``, keeping keys visible for debugging."""
    return {key: "***" for key in mapping}


@dataclass(frozen=True)
class StdioConfig:
    """Spawn a subprocess MCP server and talk to it over stdio."""

    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"StdioConfig(command={self.command!r}, args={self.args!r}, "
            f"env={_redact(self.env)!r})"
        )


@dataclass(frozen=True)
class HttpConfig:
    """Connect to a remote MCP server over Streamable HTTP."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"HttpConfig(url={self.url!r}, headers={_redact(self.headers)!r})"


ServerConfig = StdioConfig | HttpConfig
"""Discriminated by type: ``isinstance`` checks route each variant."""


class McpSettings(BaseSettings):
    """Env-backed global defaults (``OCTAVE_MCP_*`` vars / ``.env``)."""

    model_config = SettingsConfigDict(
        env_prefix="OCTAVE_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    request_timeout_seconds: float = 30.0

    restart_base_delay_seconds: float = 1.0
    """First auto-restart delay; exponential backoff multiplies by 2 per attempt."""

    restart_max_delay_seconds: float = 60.0
    """Cap on the backoff delay."""

    restart_max_attempts: int = 5
    """Consecutive failed restart cycles before a server enters 'crashed'."""

    probe_timeout_seconds: float = 5.0
    """Budget for the manager's confirming ping after a request timeout."""

    stabilization_seconds: float = 60.0
    """Time a connection must hold before the failure counter resets."""
