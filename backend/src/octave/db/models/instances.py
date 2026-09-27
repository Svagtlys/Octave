"""Agent runtime instances: ephemeral bindings of a definition to a session.

Rows exist only while the instance can take turns; destroy is a hard delete
(design spec 2026-09-27). No context lives on this table: live context is
the session transcript, long-term memory is the vault.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from octave.db.models.base import Base, UTCDateTime, utcnow

__all__ = ["AgentInstance"]


class AgentInstance(Base):
    """One live binding of an agent definition to one session.

    ``agent_id`` is RESTRICT: deleting a definition with live instances is
    the app-guarded door recorded in the design spec. ``session_id`` is
    CASCADE: a session ending destroys its instances.
    """

    __tablename__ = "agent_instances"
    __table_args__ = (
        UniqueConstraint(
            "agent_id", "session_id", name="uq_agent_instances_agent_session"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        ForeignKey("agents.id", ondelete="RESTRICT"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="idle")
    """``idle | active`` (InstanceStatus, app-validated; TEXT by house
    style — cf. events.kind rationale)."""
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
