"""Database configuration.

Two layers, mirroring ``octave.inference.config``: ``DbConfig`` is what the
registry and adapters consume — frozen, safe to share. ``DatabaseSettings`` is
env-backed bootstrap config (``OCTAVE_DB_*`` vars / ``.env``).

No secret material lives here; ``mcp_servers.env`` secrets are rows, not config.
"""

from dataclasses import dataclass

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["DbConfig", "DatabaseSettings"]


@dataclass(frozen=True)
class DbConfig:
    """Everything an adapter needs to connect. Frozen; safe to share."""

    adapter: str
    """Registered adapter name or a ``module.path:ClassName`` import string."""

    url: str
    """Async SQLAlchemy URL, e.g. ``sqlite+aiosqlite:///octave.db``."""

    embedding_dim: int
    """Width of the vector index this deployment expects."""

    @property
    def sync_url(self) -> str:
        """Sync mirror of ``url`` for Alembic, which drives a sync engine.

        Only SQLite is implemented; a future pgvector adapter adds its own
        driver mapping rather than a string substitution.
        """
        if self.url.startswith("sqlite+aiosqlite://"):
            return self.url.replace("sqlite+aiosqlite://", "sqlite://", 1)
        raise NotImplementedError(
            f"sync_url is only derived for sqlite URLs, got {self.url!r}"
        )


class DatabaseSettings(BaseSettings):
    """Env-backed bootstrap config (``OCTAVE_DB_*`` vars / ``.env``)."""

    model_config = SettingsConfigDict(
        env_prefix="OCTAVE_DB_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    adapter: str = "sqlite"
    url: str = "sqlite+aiosqlite:///octave.db"
    embedding_dim: int = Field(default=768, gt=0)

    auto_migrate: bool = True
    """Apply pending migrations at startup. ``False`` skips the upgrade but
    still verifies the DB is migrated (see ``octave.db.lifespan``)."""

    def to_db_config(self) -> DbConfig:
        """Project these settings into the adapter-facing config object."""
        return DbConfig(
            adapter=self.adapter,
            url=self.url,
            embedding_dim=self.embedding_dim,
        )
