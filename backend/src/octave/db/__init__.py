"""Octave persistence layer — adapter-seamed, vector-capable storage.

Public vocabulary: ``DbAdapter`` + ``default_registry`` for engine selection,
``DatabaseSettings`` for configuration, ``upgrade`` for migrations, and the ORM
models for CRUD.

Quarantine rule (see design spec): only ``sqlite_adapter.py`` and
``_bootstrap.py`` import the vec0 extension module / do SQLite extension
loading. Everything else here is engine-neutral SQLAlchemy + Alembic.
"""

from octave.db.adapter import DbAdapter
from octave.db.config import DatabaseSettings, DbConfig
from octave.db.errors import DbError
from octave.db.lifespan import db_lifespan
from octave.db.migrations import current, upgrade
from octave.db.models import Base
from octave.db.registry import DbAdapterRegistry, default_registry, register_db

# Imported for adapter registration side effects (registers "sqlite").
from octave.db.sqlite_adapter import SqliteVecAdapter  # noqa: F401
from octave.db.types import EventKind, VectorHit

__all__ = [
    "Base",
    "DbAdapter",
    "DbAdapterRegistry",
    "DbConfig",
    "DbError",
    "DatabaseSettings",
    "EventKind",
    "SqliteVecAdapter",
    "VectorHit",
    "current",
    "db_lifespan",
    "default_registry",
    "register_db",
    "upgrade",
]
