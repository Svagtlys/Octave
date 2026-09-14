"""Identity tables: users, agents, and the participants supertype.

``participants`` is class-table inheritance over ``users`` and ``agents``:
exactly one of the two FKs is non-null, giving ``events.author_participant_id``
a single FK target. A2A is then just an event authored by one participant and
addressed to another — no special machinery.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from octave.db.models.base import Base, UTCDateTime, utcnow

__all__ = ["Agent", "Participant", "User"]


class User(Base):
    """The human who owns Octave's data. Local-first: exactly one row today.

    Deliberately minimal — preferences are ``vault_items(kind=preference)``
    per the vault-separation ADR. This is an ownership anchor, not a profile.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class Agent(Base):
    """The Agent Registry. An agent is NOT a subtype of user: it has model
    tags and a lifecycle, and it never owns data (``sessions``/``vault_items``
    point at ``users``)."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    model_tag: Mapped[str | None] = mapped_column(Text)
    """Capability tag (``thinking``/``coding``/``quick``); tag-driven wiring."""

    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    """``active | paused | terminated`` — app-validated, TEXT by design."""

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class Participant(Base):
    """A speaking entity: exactly one of ``user_id`` / ``agent_id`` is set.

    The ``user`` / ``agent`` relationships exist so the ORM unit-of-work
    orders INSERTs correctly: flush sorting follows the relationship graph,
    NOT ``ForeignKey`` declarations, so without these edges participants
    could be inserted before the users/agents they reference (loud
    ``IntegrityError`` under ``PRAGMA foreign_keys=ON``). They declare no
    schema — column definitions remain the single source of DDL.
    """

    __tablename__ = "participants"
    __table_args__ = (
        CheckConstraint(
            "(user_id IS NULL) != (agent_id IS NULL)",
            name="ck_participants_exactly_one_identity",
        ),
        UniqueConstraint("user_id", name="uq_participants_user"),
        UniqueConstraint("agent_id", name="uq_participants_agent"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE")
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    """Display name within sessions; denormalised for transcript rendering."""

    # Flush sorting follows the relationship graph, not ForeignKey
    # declarations; without these edges the unit-of-work can emit the
    # participants INSERT before the users/agents INSERTs it references.
    user: Mapped[User | None] = relationship("User", foreign_keys=[user_id])
    agent: Mapped[Agent | None] = relationship("Agent", foreign_keys=[agent_id])
