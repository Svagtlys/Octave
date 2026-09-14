"""Octave persistence layer — adapter-seamed, vector-capable storage.

Quarantine rule (see design spec): only ``sqlite_adapter.py`` and
``_bootstrap.py`` touch ``sqlite_vec`` / SQLite extension loading. Everything
else here is engine-neutral SQLAlchemy + Alembic.
"""
