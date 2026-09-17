"""Env-backed DB settings and the frozen adapter-facing config."""

import pytest
from pydantic import ValidationError

from octave.db.config import DatabaseSettings, DbConfig


def test_defaults_are_local_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OCTAVE_DB_URL", "OCTAVE_DB_EMBEDDING_DIM", "OCTAVE_DB_ADAPTER"):
        monkeypatch.delenv(var, raising=False)
    settings = DatabaseSettings(_env_file=None)
    assert settings.adapter == "sqlite"
    assert settings.url == "sqlite+aiosqlite:///octave.db"
    assert settings.embedding_dim == 768


def test_reads_octave_db_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTAVE_DB_URL", "sqlite+aiosqlite:///elsewhere.db")
    monkeypatch.setenv("OCTAVE_DB_EMBEDDING_DIM", "1024")
    monkeypatch.setenv("OCTAVE_DB_ADAPTER", "pgvector")
    settings = DatabaseSettings(_env_file=None)
    assert settings.url == "sqlite+aiosqlite:///elsewhere.db"
    assert settings.embedding_dim == 1024
    assert settings.adapter == "pgvector"


def test_embedding_dim_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTAVE_DB_EMBEDDING_DIM", "0")
    with pytest.raises(ValidationError):
        DatabaseSettings(_env_file=None)


def test_to_db_config_projects_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTAVE_DB_EMBEDDING_DIM", "512")
    config = DatabaseSettings(_env_file=None).to_db_config()
    assert config == DbConfig(
        adapter="sqlite",
        url="sqlite+aiosqlite:///octave.db",
        embedding_dim=512,
    )


def test_sync_url_strips_async_driver() -> None:
    config = DbConfig(
        adapter="sqlite", url="sqlite+aiosqlite:///octave.db", embedding_dim=768
    )
    assert config.sync_url == "sqlite:///octave.db"


def test_sync_url_rejects_non_sqlite() -> None:
    config = DbConfig(
        adapter="pgvector",
        url="postgresql+asyncpg://localhost/octave",
        embedding_dim=768,
    )
    with pytest.raises(NotImplementedError):
        config.sync_url


def test_config_is_frozen() -> None:
    config = DbConfig(
        adapter="sqlite", url="sqlite+aiosqlite:///:memory:", embedding_dim=4
    )
    with pytest.raises((AttributeError, TypeError)):
        config.embedding_dim = 8  # type: ignore[misc]


def test_auto_migrate_defaults_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OCTAVE_DB_AUTO_MIGRATE", raising=False)
    assert DatabaseSettings(_env_file=None).auto_migrate is True


def test_auto_migrate_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTAVE_DB_AUTO_MIGRATE", "false")
    assert DatabaseSettings(_env_file=None).auto_migrate is False
