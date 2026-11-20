"""Octave DB exception hierarchy.

Vendor exceptions (sqlite3, the vector extension, SQLAlchemy, Alembic) must
never escape ``octave.db``; they are translated at the adapter/migration
boundary. Same fail-loud philosophy as ``octave.inference.errors`` and
``octave.mcp.errors``.
"""

from collections.abc import Iterable

__all__ = [
    "DbAdapterLoadError",
    "DbAdapterRegistrationError",
    "DbConfigError",
    "DbDimensionMismatchError",
    "DbError",
    "DbMigrationError",
    "OwnerParticipantMissingError",
    "UnknownDbAdapterError",
]


class DbError(Exception):
    """Base class for every persistence-layer failure."""


class DbConfigError(DbError):
    """Configuration is malformed (raised before any I/O)."""


class DbMigrationError(DbError):
    """An Alembic migration failed to apply."""


class DbDimensionMismatchError(DbError):
    """A vector was stored or queried at the wrong width.

    vec0/pgvector cannot pad or truncate, so this is a hard error rather than
    something to repair silently: the caller must re-embed.
    """

    def __init__(self, *, expected: int, actual: int) -> None:
        super().__init__(
            f"embedding has {actual} dimensions, but the vector store "
            f"is indexed at {expected}; re-embed before searching"
        )
        self.expected = expected
        self.actual = actual


class DbAdapterRegistrationError(DbError):
    """An adapter name is already registered."""


class DbAdapterLoadError(DbError):
    """An import-string adapter could not be loaded."""


class OwnerParticipantMissingError(DbError):
    """A user has no Participant row — the invariant is broken.

    ``AuthStore.create_user`` and the auth backfill migration guarantee one
    Participant per user; a missing row is a data-integrity bug. Fail loud;
    never self-heal by inserting a Participant on the fly (issue #121).
    """

    def __init__(self, user_id: str) -> None:
        super().__init__(f"user {user_id!r} has no participant row")
        self.user_id = user_id


class UnknownDbAdapterError(DbError):
    """No adapter is registered under the requested name."""

    def __init__(self, name: str, known: Iterable[str]) -> None:
        self.name = name
        self.known = sorted(known)
        super().__init__(f"unknown db adapter {name!r}; known adapters: {self.known}")
