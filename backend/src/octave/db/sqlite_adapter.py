"""SQLite + vec0 adapter — the only module that queries vec0.

Quarantine rule: ``sqlite_vec`` / vec0 SQL lives here (and in ``_bootstrap``
for extension loading). Nothing else in ``octave.db`` knows vec0 exists.

The vector index is a dim-suffixed virtual table (``vec_vault_items_<N>``):
the dimension is baked into vec0's DDL and cannot be altered, so naming by
dimension makes staleness a name lookup and lets old/new coexist during a
rebuild. It is adapter-private and deliberately NOT Alembic-managed.
"""

import logging
from collections.abc import Sequence

import sqlite_vec
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from octave.db._bootstrap import make_async_creator
from octave.db.adapter import DbAdapter
from octave.db.errors import DbDimensionMismatchError, DbError
from octave.db.registry import register_db
from octave.db.types import VectorHit

__all__ = ["SqliteVecAdapter", "vector_table_name"]

logger = logging.getLogger(__name__)


def vector_table_name(dim: int) -> str:
    """Name of the vec0 virtual table for a given embedding width."""
    return f"vec_vault_items_{dim}"


@register_db("sqlite")
class SqliteVecAdapter(DbAdapter):
    """SQLite engine + vec0 vector index."""

    def make_engine(self) -> AsyncEngine:
        creator = make_async_creator(make_url(self._config.url))
        return create_async_engine("sqlite+aiosqlite://", async_creator=creator)

    def make_session_factory(
        self, engine: AsyncEngine
    ) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(engine, expire_on_commit=False)

    def _dim(self, dim: int | None) -> int:
        """Resolve the working dimension.

        ``ensure_vector_store`` takes an explicit ``dim``; ``search_similar``
        always uses the configured one — searching an index that was not
        built at the configured width is a deployment bug, not a choice.
        """
        return self._config.embedding_dim if dim is None else dim

    async def _existing_vector_dims(self, connection: AsyncConnection) -> list[int]:
        rows = (
            await connection.execute(
                text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name LIKE 'vec_vault_items_%'"
                )
            )
        ).all()
        dims: list[int] = []
        for (name,) in rows:
            suffix = str(name).rsplit("_", 1)[-1]
            if suffix.isdigit():
                dims.append(int(suffix))
        return dims

    async def ensure_vector_store(
        self, connection: AsyncConnection, *, dim: int | None = None
    ) -> None:
        target = self._dim(dim)
        existing = await self._existing_vector_dims(connection)
        if target in existing:
            return
        stale = [d for d in existing if d != target]
        for stale_dim in stale:
            logger.warning(
                "dropping stale vector index vec_vault_items_%s; vault "
                "re-embedding is pending at dim %s",
                stale_dim,
                target,
            )
            await connection.execute(
                text(f"DROP TABLE IF EXISTS {vector_table_name(stale_dim)}")
            )
        await connection.execute(
            text(
                f"CREATE VIRTUAL TABLE {vector_table_name(target)} USING vec0("
                "item_id TEXT PRIMARY KEY, "
                f"embedding float[{target}] distance_metric=cosine, "
                "kind TEXT, user_id TEXT, session_id TEXT)"
            )
        )
        logger.info("created vector index %s", vector_table_name(target))

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
        target = self._dim(None)
        if len(embedding) != target:
            raise DbDimensionMismatchError(expected=target, actual=len(embedding))
        # vec0 0.1.x does not support INSERT OR REPLACE (unique-constraint
        # error on re-insert), so replace = delete-then-insert; the caller's
        # transaction keeps the pair atomic.
        try:
            await connection.execute(
                text(f"DELETE FROM {vector_table_name(target)} WHERE item_id = :id"),
                {"id": item_id},
            )
            await connection.execute(
                text(
                    f"INSERT INTO {vector_table_name(target)}"
                    "(item_id, embedding, kind, user_id, session_id) "
                    "VALUES (:id, :vec, :kind, :user_id, :session_id)"
                ),
                {
                    "id": item_id,
                    "vec": sqlite_vec.serialize_float32(list(embedding)),
                    # vec0 0.1.x rejects NULL in TEXT aux columns, so absent
                    # filters are stored as "" (never matches a real filter
                    # value; unfiltered searches return every row anyway).
                    "kind": kind or "",
                    "user_id": user_id or "",
                    "session_id": session_id or "",
                },
            )
        except Exception as exc:  # vendor errors never escape (errors.py rule)
            raise DbError(f"vec0 store_vector failed: {exc}") from exc

    async def remove_vector(self, connection: AsyncConnection, *, item_id: str) -> None:
        try:
            await connection.execute(
                text(f"DELETE FROM {vector_table_name(self._dim(None))} "
                     "WHERE item_id = :id"),
                {"id": item_id},
            )
        except Exception as exc:
            raise DbError(f"vec0 remove_vector failed: {exc}") from exc

    async def search_similar(
        self,
        connection: AsyncConnection,
        embedding: Sequence[float],
        *,
        limit: int = 10,
    ) -> list[VectorHit]:
        target = self._dim(None)
        if len(embedding) != target:
            raise DbDimensionMismatchError(expected=target, actual=len(embedding))
        table = vector_table_name(target)
        rows = (
            await connection.execute(
                text(
                    f"SELECT item_id, distance FROM {table} "
                    "WHERE embedding MATCH :query AND k = :k ORDER BY distance"
                ),
                {
                    "query": sqlite_vec.serialize_float32(list(embedding)),
                    "k": limit,
                },
            )
        ).all()
        return [VectorHit(item_id=str(row[0]), distance=float(row[1])) for row in rows]
