"""MCP Connector configuration persistence.

Replaces the env-file config story. ``env`` may carry secrets — never log it;
redact ``***`` per the coding rules.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from octave.db.models.base import Base, UTCDateTime, utcnow

__all__ = ["McpServer"]


class McpServer(Base):
    """One configured MCP server. Transport shape is enforced in the DB:
    stdio requires ``command``, http requires ``url``, and the two are
    mutually exclusive."""

    __tablename__ = "mcp_servers"
    __table_args__ = (
        UniqueConstraint("name", name="uq_mcp_servers_name"),
        CheckConstraint(
            "(transport = 'stdio' AND command IS NOT NULL AND url IS NULL) OR "
            "(transport = 'http' AND url IS NOT NULL AND command IS NULL)",
            name="ck_mcp_servers_transport_shape",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    transport: Mapped[str] = mapped_column(Text, nullable=False)
    """``stdio | http``."""

    command: Mapped[str | None] = mapped_column(Text)
    args: Mapped[list[str] | None] = mapped_column(JSON)
    url: Mapped[str | None] = mapped_column(Text)
    env: Mapped[dict[str, str] | None] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
