"""Declarative base and the single timestamp source.

Lives apart from ``models/__init__.py`` to avoid the circular import between
the package re-export and the model modules.

Every constraint in this package carries an explicit, self-describing name
(``ck_participants_exactly_one_identity``, ``uq_events_session_seq``, ...)
instead of relying on a ``MetaData`` naming convention: names must be stable so
later Alembic revisions can ``op.drop_constraint`` them, and an explicit name
reads better in a generated migration than a convention template does.
"""

from datetime import UTC, datetime

from sqlalchemy import Text, TypeDecorator
from sqlalchemy.orm import DeclarativeBase

__all__ = ["Base", "UTCDateTime", "utcnow"]


def utcnow() -> datetime:
    """Timezone-aware UTC now — the single timestamp source for all rows."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timestamp that round-trips UTC-aware through SQLite.

    SQLite has no native datetime type, and SQLAlchemy's default ``DATETIME``
    bind format drops the UTC offset: values re-loaded from disk come back
    naive, which breaks every caller that compares against ``utcnow()``.
    This decorator stores the ISO-8601 string *with* its offset and
    re-attaches UTC on load, so timestamps are tz-aware whether they come
    from the identity map or a fresh SELECT.
    """

    # ``Text`` (not ``DateTime``) as impl: the bind processor hands the ISO
    # string to the impl, and SQLite's DATETIME bind rejects non-datetime
    # input. SQLite's dynamic typing makes TEXT and DATETIME equivalent in
    # affinity; the decorator is the single conversion point either way.
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat(sep=" ", timespec="microseconds")

    def process_result_value(
        self, value: str | None, dialect: object
    ) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


class Base(DeclarativeBase):
    """Base class for every ORM model in the package."""
