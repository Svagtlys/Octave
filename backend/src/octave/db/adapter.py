"""The adapter contract every database engine implements.

Deliberately thin, mirroring ``octave.inference.adapter``: the adapter owns
engine/session lifecycle, vector-store DDL, and similarity search. Per-table
CRUD is engine-neutral SQLAlchemy ORM work and stays OUT of this interface —
SQLAlchemy already is that abstraction; wrapping it would re-abstract it.

Implementations must be safe for concurrent use within one event loop.
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from octave.db.config import DbConfig
from octave.db.types import VectorHit

__all__ = ["DbAdapter"]


class DbAdapter(ABC):
    """Contract for database engines. SQLite + vec0 is the first implementation."""

    def __init__(self, config: DbConfig) -> None:
        self._config = config

    @property
    def config(self) -> DbConfig:
        """The configuration this adapter was created with."""
        return self._config

    @abstractmethod
    def make_engine(self) -> AsyncEngine:
        """Create the async engine for ``self.config.url``.

        Implementations must wire whatever the engine needs to function (for
        SQLite: the vec0 extension and pragmas — see ``octave.db._bootstrap``).
        """

    @abstractmethod
    def make_session_factory(
        self, engine: AsyncEngine
    ) -> async_sessionmaker[AsyncSession]:
        """Build the session factory callers share. Expire-on-commit off so
        rows stay readable after ``commit()`` without a re-load."""

    @abstractmethod
    async def ensure_vector_store(
        self, connection: AsyncConnection, *, dim: int | None = None
    ) -> None:
        """Create the vector index for ``dim`` (default: the config's).

        Idempotent. If an index exists at a different dimension, drop it and
        create the requested one, logging WARN that vault re-embedding is
        pending — vec0 cannot pad or truncate, so a dimension change is always
        a full rebuild. The caller owns the transaction.
        """

    async def store_vector(
        self,
        connection: AsyncConnection,
        *,
        item_id: str,
        embedding: Sequence[float],
        kind: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        """Mirror one item's vector (and filter metadata) into the vector store.

        Idempotent: storing the same ``item_id`` twice replaces, never duplicates.
        Raises ``DbDimensionMismatchError`` on width mismatch. The caller owns
        the transaction — pair this with the ``vault_items`` flush.

        No-op default: engines whose vectors live in the table itself
        (pgvector's column + native index) have nothing to mirror and only
        implement ``search_similar``. The conformance suite pins the contract:
        after ``store_vector``, ``search_similar`` must find the item.
        """
        return None

    async def remove_vector(self, connection: AsyncConnection, *, item_id: str) -> None:
        """Drop one item from the vector store. Unknown ids are silent no-ops.

        No-op default for the same reason as ``store_vector``.
        """
        return None

    @abstractmethod
    async def search_similar(
        self,
        connection: AsyncConnection,
        embedding: Sequence[float],
        *,
        limit: int = 10,
        kind: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[VectorHit]:
        """Nearest neighbours, ascending distance, optionally filtered.

        Filters are applied inside the ANN scan (before ``limit``) — a
        filtered search returns up to ``limit`` TRUE matches, never fewer
        due to post-scan drops. ``None`` means unfiltered (the pre-#32
        behavior). Raises ``DbDimensionMismatchError`` when
        ``len(embedding)`` does not match the configured index width.
        """

    async def aclose(self) -> None:
        """Release resources. Concrete no-op default; adapters override."""
        return None
