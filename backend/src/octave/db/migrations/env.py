"""Alembic environment.

Runs on a sync engine. The URL arrives two ways: ``octave.db.migrations.upgrade``
passes it as ``config.attributes["sync_url"]``; the CLI falls back to
``alembic.ini`` / ``OCTAVE_DB_URL``.

``render_as_batch`` is mandatory for SQLite: without it Alembic emits
``ALTER TABLE`` forms SQLite does not support.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from octave.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    """Resolve the sync URL: programmatic attribute, else ini, else env."""
    attribute_url = config.attributes.get("sync_url")
    if attribute_url:
        return str(attribute_url)
    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url:
        return str(ini_url)
    from octave.db.config import DatabaseSettings

    return DatabaseSettings().to_db_config().sync_url


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _sync_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


# context.is_offline_mode() is True for `alembic upgrade --sql`; do not key off
# an env var, which would desync from the CLI's actual mode.
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
