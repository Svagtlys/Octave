"""Context Vault items.

Three-tier data model: ``content`` is the truth; ``embedding`` is an
engine-neutral cache (little-endian float32 BLOB); ``vec_vault_items_<N>`` is
the adapter-private derived index. ``embedding_model`` + ``embedding_dim``
make stale embeddings detectable against ``OCTAVE_DB_EMBEDDING_DIM``.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from octave.db.models.base import Base, UTCDateTime, utcnow
from octave.db.models.core import User

__all__ = ["VaultItem"]


class VaultItem(Base):
    """One vault entry: skill | prompt | preference | agent_state."""

    __tablename__ = "vault_items"
    __table_args__ = (Index("ix_vault_items_user_kind", "user_id", "kind"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    """Ownership AND vector-search scoping."""

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    """SQL column ``metadata`` — the ORM attribute is ``meta`` because
    ``metadata`` is reserved on ``DeclarativeBase``. Tags live here for now
    (no ``vault_tags`` table)."""

    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_model: Mapped[str | None] = mapped_column(Text)
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    # Flush-sorting edge (see SessionParticipant): vault items must flush
    # after their owning user when both are new in one transaction.
    user: Mapped[User] = relationship("User", foreign_keys=[user_id])
