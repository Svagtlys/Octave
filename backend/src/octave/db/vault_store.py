"""Context vault storage layer — write-path invariants for ``vault_items``.

Pairs every ORM write with the engine's vector-store mirror through the
adapter seam (``store_vector``/``remove_vector``) inside the caller's
transaction, enforces the embedding-cache staleness rule (content is the
source of truth; a new ``content`` without a new ``embedding`` invalidates
the cache), and validates ``kind``/``meta`` at write time.

Never commits — callers own transaction boundaries (see ``octave.db.deps``).
Embeddings are caller-supplied: this module must never import
``octave.inference`` (design Decision 1).
"""

import struct
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from octave.db.adapter import DbAdapter
from octave.db.errors import DbConfigError, DbDimensionMismatchError
from octave.db.models import VaultItem
from octave.db.types import VaultKind

__all__ = ["VaultStore"]


def _serialize_float32_le(vector: Sequence[float]) -> bytes:
    """Engine-neutral float32 little-endian BLOB (vec cache column)."""
    return struct.pack(f"<{len(vector)}f", *vector)


class VaultStore:
    """CRUD + filtered vector search over ``vault_items``.

    Constructed per-request with the caller's ``AsyncSession``; the vec
    mirror rides the session's own connection, so row and mirror commit or
    roll back together.
    """

    def __init__(self, adapter: DbAdapter, session: AsyncSession) -> None:
        self._adapter = adapter
        self._session = session

    async def upsert(
        self,
        *,
        item_id: str,
        user_id: str,
        kind: VaultKind,
        name: str,
        content: str,
        meta: Mapping[str, Any] | None = None,
        embedding: Sequence[float] | None = None,
        embedding_model: str | None = None,
    ) -> VaultItem:
        """Full-replacement idempotent write (design Decision 4).

        Validation before any write: ``kind`` revalidated against
        ``VaultKind`` (bare strings rejected); ``meta`` must be a mapping;
        embedding width must match ``config.embedding_dim``;
        ``embedding_model`` without ``embedding`` is incoherent.
        """
        kind = VaultKind(kind)  # app-level revalidation, ValueError on garbage
        if meta is not None and not isinstance(meta, Mapping):
            raise DbConfigError("meta must be a JSON object (mapping) or None")
        if embedding_model is not None and embedding is None:
            raise DbConfigError("embedding_model requires an embedding to cache")
        if embedding is not None:
            dim = self._adapter.config.embedding_dim
            if len(embedding) != dim:
                raise DbDimensionMismatchError(expected=dim, actual=len(embedding))
        meta_dict: dict[str, Any] = dict(meta) if meta is not None else {}

        item = await self._session.get(VaultItem, item_id)
        if item is None:
            item = VaultItem(id=item_id)
            self._session.add(item)
        item.user_id = user_id
        item.kind = str(kind)
        item.name = name
        item.content = content
        item.meta = meta_dict
        if embedding is not None:
            item.embedding = _serialize_float32_le(embedding)
            item.embedding_model = embedding_model
            item.embedding_dim = len(embedding)
        else:
            item.embedding = None
            item.embedding_model = None
            item.embedding_dim = None

        await self._session.flush()
        connection = await self._session.connection()
        if embedding is not None:
            raw_session_id = meta_dict.get("session_id")
            await self._adapter.store_vector(
                connection,
                item_id=item_id,
                embedding=embedding,
                kind=str(kind),
                user_id=user_id,
                session_id=raw_session_id if isinstance(raw_session_id, str) else None,
            )
        else:
            await self._adapter.remove_vector(connection, item_id=item_id)
        return item

    async def delete(self, item_id: str) -> bool:
        """Remove row + mirror in one transaction. False if absent."""
        item = await self._session.get(VaultItem, item_id)
        if item is None:
            return False
        await self._session.delete(item)
        await self._session.flush()
        connection = await self._session.connection()
        await self._adapter.remove_vector(connection, item_id=item_id)
        return True

    async def get(self, item_id: str) -> VaultItem | None:
        """Load one item by id, or None."""
        return await self._session.get(VaultItem, item_id)
