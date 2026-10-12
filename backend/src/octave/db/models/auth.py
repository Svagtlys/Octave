"""Auth-plane tables: server-side session tokens (issue #122).

``auth_sessions.id`` is the sha256 hex of the raw token — the raw token only
ever exists in a ``Set-Cookie`` response. Deactivation revokes by deleting
rows (instant logout); expiry is sliding idle with an absolute cap, enforced
by the auth service (see ``octave.auth``).
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from octave.db.models.base import Base, UTCDateTime, utcnow
from octave.db.models.core import User

__all__ = ["AuthSession"]


class AuthSession(Base):
    """One authenticated browser session for one user."""

    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_id", "user_id"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    """sha256(token) hex — never the raw token."""
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # Flush sorting follows the relationship graph, not ForeignKey
    # declarations; without this edge the unit-of-work can emit the
    # auth_sessions INSERT before the users INSERT it references.
    user: Mapped[User] = relationship("User")
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    """Sliding expiry: extends on authenticated use up to the absolute cap."""
    last_seen_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
