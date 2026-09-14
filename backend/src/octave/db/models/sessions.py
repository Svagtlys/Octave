"""Session transcript tables: sessions, membership, events.

Vocabulary per the design spec: ``sessions`` (not ``conversations``) because
an LLM run thread is not necessarily a conversation; ``events`` (not
``messages``) because entries are typed and not all text. Shipped runtime
shape is 1 user + 1 agent chat; multi-party/A2A is reachable via these tables
without re-modeling, but no orchestration columns ship (see spec Non-goals).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from octave.db.models.base import Base, UTCDateTime, utcnow
from octave.db.models.core import Participant

__all__ = ["Event", "Session", "SessionParticipant"]


class Session(Base):
    """Any thread of LLM-driven activity.

    ``created_by_user_id`` is the single owner (listing, cascade, backup
    scoping) — NOT the participant set, which is N via membership. NOT NULL:
    even an autonomous run traces to a human owner; a NULL would be an
    orphaned-session bug, so the constraint is the fail-loud choice.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_created_by_user", "created_by_user_id"),
        Index("ix_sessions_parent", "parent_session_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    created_by_user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    parent_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )
    """Sub-agent lineage. NULL for top-level chat. Spawning a sub-agent later
    = create child session (+ parent-side reference event, deferred)."""

    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    """``active | waiting | completed | failed | cancelled`` (app-validated)."""

    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class SessionParticipant(Base):
    """Membership: identity (participant) × place (session), with a role.

    ``speaker`` may emit events; ``observer`` watches. ``left_at`` NULL =
    current member. N humans + M agents is expressible with zero schema change.
    """

    __tablename__ = "session_participants"
    __table_args__ = (
        PrimaryKeyConstraint(
            "session_id", "participant_id", name="pk_session_participants"
        ),
    )

    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    participant_id: Mapped[str] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, default="speaker")
    joined_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    left_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    # Flush sorting follows the relationship graph, not ForeignKey
    # declarations; without these edges membership rows can flush before the
    # sessions/participants rows they reference.
    session: Mapped[Session] = relationship("Session", foreign_keys=[session_id])
    participant: Mapped[Participant] = relationship(
        "Participant", foreign_keys=[participant_id]
    )


class Event(Base):
    """One transcript entry. ``kind`` is TEXT with app-level enum validation
    (``octave.db.types.EventKind``) — a DB CHECK would force a table rebuild
    per new kind. ``payload`` integrity lives in Pydantic models per kind."""

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_events_session_seq"),
        # Composite FK: an event may only address a member of its own session.
        # NULL target = broadcast (SQLite FK is MATCH SIMPLE, so NULLs pass).
        # ondelete CASCADE: membership rows are only removed when the session
        # or participant dies, which cascades to the event anyway.
        ForeignKeyConstraint(
            ["session_id", "target_participant_id"],
            [
                "session_participants.session_id",
                "session_participants.participant_id",
            ],
            name="fk_events_target_membership",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    """Per-session monotonic (gap-free) ordering for replay and turn grouping;
    clock resolution cannot guarantee this."""

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    author_participant_id: Mapped[str | None] = mapped_column(
        ForeignKey("participants.id", ondelete="CASCADE")
    )
    """NULL allowed for ``system`` events."""

    target_participant_id: Mapped[str | None] = mapped_column(Text)
    """NULL = broadcast. Always NULL in shipped 1:1 scope; kept now because
    adding a composite FK later requires an SQLite batch-mode rebuild."""

    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    # Flush-sorting edges (see SessionParticipant): events must flush after
    # their session and author. The composite FK target is membership, not a
    # single table, so ``target_participant_id`` stays plain Text with no
    # relationship — the DB constraint still enforces membership.
    session: Mapped[Session] = relationship("Session", foreign_keys=[session_id])
    author: Mapped[Participant | None] = relationship(
        "Participant", foreign_keys=[author_participant_id]
    )
