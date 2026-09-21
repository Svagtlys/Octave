"""Reusable DbAdapter conformance suite — the anti-over-tailoring enforcement.

Any adapter claiming to satisfy ``DbAdapter`` must pass these. Mirrors
``tests/inference/conformance.py``. Deliberately coarse: it tests the contract,
not vec0 specifics (dim-change behaviour lives in test_sqlite_adapter.py).
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from octave.db.adapter import DbAdapter
from octave.db.types import VectorHit

__all__ = ["run_db_adapter_conformance"]


async def run_db_adapter_conformance(
    adapter: DbAdapter, engine: AsyncEngine
) -> None:
    """Assert the ``DbAdapter`` contract against a live engine."""
    dim = adapter.config.embedding_dim
    assert dim >= 2, "conformance fixture needs dim >= 2"

    async with engine.connect() as conn:
        version = (
            await conn.execute(text("SELECT 1"))
        ).scalar_one()
    assert version == 1, "engine must be usable"

    async with engine.begin() as conn:
        await adapter.ensure_vector_store(conn)
        await adapter.ensure_vector_store(conn)  # idempotent

    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"INSERT INTO vec_vault_items_{dim}"
                "(item_id, embedding, kind, user_id, session_id) "
                "VALUES (:id, :vec, '', '', '')"
            ),
            {"id": "c_1", "vec": _serialize([1.0] + [0.0] * (dim - 1))},
        )
        hits = await adapter.search_similar(
            conn, [1.0] + [0.0] * (dim - 1), limit=5
        )
    assert isinstance(hits, list)
    assert all(isinstance(hit, VectorHit) for hit in hits)
    assert [hit.item_id for hit in hits] == ["c_1"]

    # Mirror contract: store_vector -> findable; remove_vector -> gone.
    probe = [0.0, 1.0] + [0.0] * (dim - 2)
    async with engine.begin() as conn:
        await adapter.store_vector(
            conn, item_id="c_mirror", embedding=probe,
            kind="prompt", user_id="u_conf",
        )
        hits = await adapter.search_similar(conn, probe, limit=10)
        assert "c_mirror" in [hit.item_id for hit in hits]
        # Filter visibility: stored as prompt, invisible to a skill filter.
        hits = await adapter.search_similar(conn, probe, limit=10, kind="skill")
        assert "c_mirror" not in [hit.item_id for hit in hits]
        hits = await adapter.search_similar(conn, probe, limit=10, kind="prompt")
        assert "c_mirror" in [hit.item_id for hit in hits]
        await adapter.remove_vector(conn, item_id="c_mirror")
        hits = await adapter.search_similar(conn, probe, limit=10)
        assert "c_mirror" not in [hit.item_id for hit in hits]

    await adapter.aclose()
    await adapter.aclose()  # must be safe to call twice


def _serialize(vector: list[float]) -> bytes:
    """Driver-neutral float32 encoding; adapters must accept this layout."""
    import struct

    return b"".join(struct.pack("<f", value) for value in vector)
