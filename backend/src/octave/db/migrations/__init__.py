"""Alembic migration environment + programmatic Alembic access.

``upgrade`` / ``current`` ship as tested callables only. Invoking them from
app startup (auto-migrate) is deliberately deferred to the lifespan work
item; see spec Decision 5.

The wrapper lives in the package ``__init__`` because ``migrations.py`` as a
sibling module would be shadowed by this package. ``env.py`` is loaded only
by Alembic at migration time, never on import of this package.
"""

import logging
from pathlib import Path

from alembic.command import upgrade as _alembic_upgrade
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine

from octave.db.errors import DbMigrationError

__all__ = ["current", "upgrade"]

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent
_INI = _HERE.parents[3] / "alembic.ini"
"""backend/alembic.ini — ``_HERE`` is ``src/octave/db/migrations``, so
``parents[3]`` is ``backend/``. Verify in Step 6: a wrong depth fails with
``FileNotFoundError: No file .../alembic.ini found``."""


def _config(sync_url: str) -> Config:
    cfg = Config(str(_INI))
    cfg.set_main_option("script_location", str(_HERE))
    cfg.attributes["sync_url"] = sync_url
    return cfg


def upgrade(sync_url: str) -> None:
    """Apply all pending migrations to ``sync_url``. Idempotent.

    Raises ``DbMigrationError`` on failure; Alembic's own exceptions never
    escape this module.
    """
    logger.info("applying migrations to %s", sync_url)
    try:
        _alembic_upgrade(_config(sync_url), "head")
    except Exception as exc:  # Alembic raises broad; translate at the boundary
        logger.exception("migration failed for %s", sync_url)
        raise DbMigrationError(f"migration failed: {exc}") from exc
    logger.info("migrations applied to %s", sync_url)


def current(sync_url: str) -> str | None:
    """Revision stamped on ``sync_url``, or None when the DB is unmigrated."""
    engine = create_engine(sync_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            revision: str | None = context.get_current_revision()
            return revision
    finally:
        engine.dispose()
