"""DbAdapterRegistry: names, import strings, duplicate registration, errors."""

from collections.abc import Sequence

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from octave.db.adapter import DbAdapter
from octave.db.config import DbConfig
from octave.db.errors import (
    DbAdapterLoadError,
    DbAdapterRegistrationError,
    UnknownDbAdapterError,
)
from octave.db.registry import DbAdapterRegistry, register_db
from octave.db.types import VectorHit


class StubAdapter(DbAdapter):
    """Minimal contract-satisfying adapter; registry tests never hit I/O."""

    def make_engine(self) -> AsyncEngine:
        raise NotImplementedError

    def make_session_factory(
        self, engine: AsyncEngine
    ) -> async_sessionmaker[AsyncSession]:
        raise NotImplementedError

    async def ensure_vector_store(
        self, connection: AsyncConnection, *, dim: int | None = None
    ) -> None:
        raise NotImplementedError

    async def search_similar(
        self,
        connection: AsyncConnection,
        embedding: Sequence[float],
        *,
        limit: int = 10,
    ) -> list[VectorHit]:
        raise NotImplementedError


def _config(adapter: str = "stub") -> DbConfig:
    return DbConfig(
        adapter=adapter, url="sqlite+aiosqlite:///:memory:", embedding_dim=4
    )


@pytest.fixture
def registry() -> DbAdapterRegistry:
    reg = DbAdapterRegistry()
    reg.register("stub", StubAdapter)
    return reg


def test_resolve_registered_name(registry: DbAdapterRegistry) -> None:
    assert registry.resolve("stub") is StubAdapter


def test_names_sorted(registry: DbAdapterRegistry) -> None:
    registry.register("another", StubAdapter)
    assert registry.names() == ["another", "stub"]


def test_duplicate_registration_fails_fast(registry: DbAdapterRegistry) -> None:
    with pytest.raises(DbAdapterRegistrationError):
        registry.register("stub", StubAdapter)


def test_create_uses_config_adapter_name(registry: DbAdapterRegistry) -> None:
    assert isinstance(registry.create(_config()), StubAdapter)


def test_unknown_name_raises_with_known_list(
    registry: DbAdapterRegistry,
) -> None:
    with pytest.raises(UnknownDbAdapterError) as exc:
        registry.resolve("pgvector")
    assert exc.value.known == ["stub"]


def test_resolve_import_string(registry: DbAdapterRegistry) -> None:
    assert registry.resolve("tests.db.test_registry:StubAdapter") is StubAdapter


def test_import_string_missing_module_raises_load_error(
    registry: DbAdapterRegistry,
) -> None:
    with pytest.raises(DbAdapterLoadError):
        registry.resolve("nope.nope:Nope")


def test_import_string_wrong_base_raises_load_error(
    registry: DbAdapterRegistry,
) -> None:
    with pytest.raises(DbAdapterLoadError):
        registry.resolve("tests.db.test_registry:_config")


def test_register_db_decorator() -> None:
    reg = DbAdapterRegistry()

    @register_db("decorated", registry=reg)
    class Decorated(StubAdapter):
        pass

    assert reg.resolve("decorated") is Decorated
