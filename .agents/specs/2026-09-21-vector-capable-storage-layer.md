# Vector-Capable Storage Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the vault storage layer — `VaultStore` (upsert/delete/get/list_items/search) enforcing write-path invariants, `DbAdapter.store_vector`/`remove_vector`, and aux-column-filtered `search_similar` — for the context vault.

**Architecture:** A new `octave.db.vault_store` module owns the ORM-write + vec-mirror pairing inside the caller's session transaction; vec mechanics stay quarantined to `sqlite_adapter.py` behind two new adapter methods with no-op defaults (pgvector inherits silence). The vec0 DDL gains `kind`/`user_id`/`session_id` auxiliary columns so filters apply pre-`k`. Embeddings are caller-supplied: `octave.db` never imports `octave.inference`.

**Tech Stack:** Python 3.13 / SQLAlchemy 2.0 async / sqlite-vec 0.1.9 (vec0) / pytest-asyncio (auto mode) / uv / ruff / mypy strict. All commands run from `backend/`.

**Spec:** [`.agents/specs/2026-09-21-vector-capable-storage-layer-design.md`](./2026-09-21-vector-capable-storage-layer-design.md)
**Branch:** `feature/vector-capable-storage-layer` · **Draft PR:** [#97](https://github.com/Svagtlys/Octave/pull/97) · **Issue:** #32

---

## Read-this-first context (for engineers with zero codebase context)

- **The seam:** [`octave/db/adapter.py`](../../backend/src/octave/db/adapter.py) defines `DbAdapter` (ABC): engine/session lifecycle, `ensure_vector_store` (DDL), `search_similar`. Per-table CRUD is deliberately OUT of it — it lands in `vault_store.py` (Task 5/6).
- **The quarantine:** only `sqlite_adapter.py` (and `_bootstrap.py`) may import `sqlite_vec`. Nothing engine-neutral may emit vec0 SQL. `vault_store.py` talks to the vec table **only** through adapter methods.
- **The three-way split:** `vault_items.content` = truth; `vault_items.embedding` = float32 little-endian BLOB cache; `vec_vault_items_<N>` = derived ANN index (dim-suffixed vec0 virtual table, not Alembic-managed).
- **vec0 is a virtual table that owns its data** — not a real index. Writes go through `INSERT`/`DELETE` on the table; KNN is `WHERE embedding MATCH :query AND k = :k`. Auxiliary columns (non-embedding columns in the DDL) are filterable inside the KNN scan — that is what makes filtered search exact (≤ k true hits).
- **BLOB encoding:** `struct.pack("<Nf", ...)` (little-endian float32) is byte-identical to `sqlite_vec.serialize_float32`. `vault_store.py` uses `struct` (engine-neutral); `sqlite_adapter.py` keeps using `serialize_float32`.
- **Existing fixtures:** [`tests/db/conftest.py`](../../backend/tests/db/conftest.py) provides `engine`/`session_factory` but does NOT create the vec table; the store tests' own fixture (Task 5) does.
- **Gates (every task):** `cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src`.
- **sqlite-vec version caveat:** if `INSERT OR REPLACE INTO vec_vault_items_…` is rejected by the installed 0.1.9, replace it with `DELETE FROM <table> WHERE item_id = :id;` followed by the plain `INSERT` in the same statement sequence (identical semantics). The Task 2 tests detect this immediately.

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `.agents/specs/2026-09-21-vector-capable-storage-layer-design.md` | commit (already written) | The approved design doc |
| `backend/src/octave/db/adapter.py` | modify | ABC: `store_vector`/`remove_vector` no-op defaults; `search_similar` gains keyword-only filters |
| `backend/src/octave/db/sqlite_adapter.py` | modify | Aux-column DDL; vec overrides with vendor-error wrapping; filtered search SQL |
| `backend/src/octave/db/vault_store.py` | **create** | `VaultStore` + `VaultHit`: upsert/delete/get/list_items/search, staleness rules, validation |
| `backend/src/octave/db/__init__.py` | modify | Re-export `VaultStore`, `VaultHit` |
| `backend/tests/db/conformance.py` | modify | Engine-neutral mirror + filter contract |
| `backend/tests/db/test_sqlite_adapter.py` | modify | Aux DDL, store/remove round-trip, filtered exactness |
| `backend/tests/db/test_vault_store.py` | **create** | Store invariants, atomicity, user scoping |
| `.agents/memory/decisions.md` | modify | ADR for this work item |
| `docs/TODO.md` | modify | Mark CM #2 done |

---

### Task 1: Commit the design document

The design doc was written in architect mode (which cannot run git). Commit it first so the PR carries the approved design before any code.

**Files:**
- Commit: `.agents/specs/2026-09-21-vector-capable-storage-layer-design.md`

- [ ] **Step 1: Verify branch and file presence**

Run: `git branch --show-current && ls .agents/specs/2026-09-21-vector-capable-storage-layer-design.md`
Expected: `feature/vector-capable-storage-layer` and the file path echoed. If the branch is wrong, `git checkout feature/vector-capable-storage-layer` first.

- [ ] **Step 2: Commit**

```bash
git add .agents/specs/2026-09-21-vector-capable-storage-layer-design.md
git commit -m "docs(context): add vector storage layer design for #32"
```

- [ ] **Step 3: Push**

```bash
git push -u origin feature/vector-capable-storage-layer
```

---

### Task 2: Adapter seam — aux-column DDL + `store_vector` / `remove_vector`

Adds the vec-write capability to the seam. ABC gets no-op defaults (mirroring `aclose`); `SqliteVecAdapter` overrides with vec0 SQL. TDD against `test_sqlite_adapter.py`.

**Files:**
- Modify: `backend/tests/db/test_sqlite_adapter.py` (append tests)
- Modify: `backend/src/octave/db/adapter.py` (after `ensure_vector_store`, before `search_similar`)
- Modify: `backend/src/octave/db/sqlite_adapter.py` (`ensure_vector_store` DDL; new methods)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/db/test_sqlite_adapter.py` (imports at top of the file already include `text`, `serialize_float32`, `DbConfig`, `DbDimensionMismatchError`, `SqliteVecAdapter`, `vector_table_name`). Add `DbError` to the errors import:

```python
from octave.db.errors import DbDimensionMismatchError, DbError
```

Append at end of file:

```python
async def test_store_vector_round_trip_and_aux_columns(tmp_path: Path) -> None:
    """store_vector writes aux columns; search finds it; remove_vector drops it."""
    adapter = SqliteVecAdapter(_config(tmp_path, dim=3))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await adapter.ensure_vector_store(conn)
            await adapter.store_vector(
                conn,
                item_id="s_1",
                embedding=[1.0, 0.0, 0.0],
                kind="prompt",
                user_id="u_1",
                session_id="sess_A",
            )
            hits = await adapter.search_similar(conn, [1.0, 0.0, 0.0], limit=5)
            assert [hit.item_id for hit in hits] == ["s_1"]
            aux = (
                await conn.execute(
                    text(
                        f"SELECT kind, user_id, session_id "
                        f"FROM {vector_table_name(3)} WHERE item_id = 's_1'"
                    )
                )
            ).one()
            assert tuple(aux) == ("prompt", "u_1", "sess_A")
            await adapter.remove_vector(conn, item_id="s_1")
            hits = await adapter.search_similar(conn, [1.0, 0.0, 0.0], limit=5)
            assert hits == []
    finally:
        await engine.dispose()


async def test_store_vector_is_idempotent_replace(tmp_path: Path) -> None:
    """Storing the same item_id twice keeps ONE row, latest vector wins."""
    adapter = SqliteVecAdapter(_config(tmp_path, dim=2))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await adapter.ensure_vector_store(conn)
            await adapter.store_vector(
                conn, item_id="s_1", embedding=[1.0, 0.0], kind="skill", user_id="u_1"
            )
            await adapter.store_vector(
                conn, item_id="s_1", embedding=[0.0, 1.0], kind="skill", user_id="u_1"
            )
            rows = (
                await conn.execute(
                    text(f"SELECT count(*) FROM {vector_table_name(2)}")
                )
            ).scalar_one()
            hits = await adapter.search_similar(conn, [0.0, 1.0], limit=5)
        assert rows == 1
        assert [hit.item_id for hit in hits] == ["s_1"]
    finally:
        await engine.dispose()


async def test_store_vector_rejects_wrong_dim(tmp_path: Path) -> None:
    adapter = SqliteVecAdapter(_config(tmp_path, dim=3))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await adapter.ensure_vector_store(conn)
            with pytest.raises(DbDimensionMismatchError):
                await adapter.store_vector(conn, item_id="s_1", embedding=[1.0, 0.0])
    finally:
        await engine.dispose()


async def test_remove_vector_unknown_id_is_silent(tmp_path: Path) -> None:
    adapter = SqliteVecAdapter(_config(tmp_path, dim=2))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await adapter.ensure_vector_store(conn)
            await adapter.remove_vector(conn, item_id="ghost")  # must not raise
    finally:
        await engine.dispose()
```

Note: `DbError` import will be unused in this task's tests (used in Task 3) — if ruff flags it, add it in Task 3 instead. Skip the `DbError` import here if unsure; ruff `F401` will catch.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/db/test_sqlite_adapter.py -v`
Expected: FAIL — `AttributeError: 'SqliteVecAdapter' object has no attribute 'store_vector'`.

- [ ] **Step 3: Extend the ABC**

In `backend/src/octave/db/adapter.py`, insert immediately after the `ensure_vector_store` abstract method (before `search_similar`):

```python
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
        """Mirror one item's vector (and filter metadata) into the vector store.

        Idempotent: storing the same ``item_id`` twice replaces, never duplicates.
        Raises ``DbDimensionMismatchError`` on width mismatch. The caller owns
        the transaction — pair this with the ``vault_items`` flush.

        No-op default: engines whose vectors live in the table itself
        (pgvector's column + native index) have nothing to mirror and only
        implement ``search_similar``. The conformance suite pins the contract:
        after ``store_vector``, ``search_similar`` must find the item.
        """
        return None

    async def remove_vector(self, connection: AsyncConnection, *, item_id: str) -> None:
        """Drop one item from the vector store. Unknown ids are silent no-ops.

        No-op default for the same reason as ``store_vector``.
        """
        return None
```

- [ ] **Step 4: Implement in the SQLite adapter**

In `backend/src/octave/db/sqlite_adapter.py`:

4a. Update the `ensure_vector_store` `CREATE VIRTUAL TABLE` call to add aux columns:

```python
        await connection.execute(
            text(
                f"CREATE VIRTUAL TABLE {vector_table_name(target)} USING vec0("
                "item_id TEXT PRIMARY KEY, "
                f"embedding float[{target}] distance_metric=cosine, "
                "kind TEXT, user_id TEXT, session_id TEXT)"
            )
        )
```

4b. Add the two overrides after `ensure_vector_store` (and import `DbError` alongside `DbDimensionMismatchError` in the errors import line):

```python
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
        try:
            await connection.execute(
                text(
                    f"INSERT OR REPLACE INTO {vector_table_name(target)}"
                    "(item_id, embedding, kind, user_id, session_id) "
                    "VALUES (:id, :vec, :kind, :user_id, :session_id)"
                ),
                {
                    "id": item_id,
                    "vec": sqlite_vec.serialize_float32(list(embedding)),
                    "kind": kind,
                    "user_id": user_id,
                    "session_id": session_id,
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
```

If `INSERT OR REPLACE` is rejected by vec0 at runtime (see caveat above), replace the single `execute` with a `DELETE` then `INSERT` (two `connection.execute` calls, same params minus the conflict clause).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/db/test_sqlite_adapter.py -v`
Expected: all PASS, including the 4 new tests. Existing `test_search_similar_ranks_nearest_first` still passes — it inserts vec rows without aux columns (NULLs), unaffected.

- [ ] **Step 6: Full gates**

Run: `cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/src/octave/db/adapter.py backend/src/octave/db/sqlite_adapter.py backend/tests/db/test_sqlite_adapter.py
git commit -m "feat(db): vec mirror seam — store_vector/remove_vector + aux columns"
```

---

### Task 3: Filtered `search_similar` (kind / user_id / session_id)

Keyword-only filters, applied pre-`k` via aux columns. `None` = today's behavior; existing call sites untouched.

**Files:**
- Modify: `backend/tests/db/test_sqlite_adapter.py`
- Modify: `backend/src/octave/db/adapter.py` (`search_similar` signature)
- Modify: `backend/src/octave/db/sqlite_adapter.py` (`search_similar` body)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/db/test_sqlite_adapter.py`:

```python
async def test_search_similar_filters_are_exact_pre_k(tmp_path: Path) -> None:
    """Filters apply INSIDE the KNN scan: with k=1 the matching item is found
    even when a closer non-matching item exists. Post-k filtering would
    return zero hits here."""
    adapter = SqliteVecAdapter(_config(tmp_path, dim=2))
    engine = adapter.make_engine()
    try:
        async with engine.begin() as conn:
            await adapter.ensure_vector_store(conn)
            await adapter.store_vector(
                conn, item_id="near_skill", embedding=[1.0, 0.0],
                kind="skill", user_id="u_1",
            )
            await adapter.store_vector(
                conn, item_id="far_prompt", embedding=[0.707, 0.707],
                kind="prompt", user_id="u_1", session_id="sess_A",
            )
            hits = await adapter.search_similar(
                conn, [1.0, 0.0], limit=1, kind="prompt"
            )
            assert [hit.item_id for hit in hits] == ["far_prompt"]
            hits = await adapter.search_similar(
                conn, [1.0, 0.0], limit=5, user_id="u_1", session_id="sess_A"
            )
            assert [hit.item_id for hit in hits] == ["far_prompt"]
            hits = await adapter.search_similar(
                conn, [1.0, 0.0], limit=5, session_id="sess_B"
            )
            assert hits == []
            hits = await adapter.search_similar(conn, [1.0, 0.0], limit=5)
            assert [hit.item_id for hit in hits] == ["near_skill", "far_prompt"]
    finally:
        await engine.dispose()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/db/test_sqlite_adapter.py::test_search_similar_filters_are_exact_pre_k -v`
Expected: FAIL — `TypeError: search_similar() got an unexpected keyword argument 'kind'`.

- [ ] **Step 3: Extend the ABC signature**

In `backend/src/octave/db/adapter.py`, replace the `search_similar` abstract signature and docstring:

```python
    @abstractmethod
    async def search_similar(
        self,
        connection: AsyncConnection,
        embedding: Sequence[float],
        *,
        limit: int = 10,
        kind: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[VectorHit]:
        """Nearest neighbours, ascending distance, optionally filtered.

        Filters are applied inside the ANN scan (before ``limit``) — a
        filtered search returns up to ``limit`` TRUE matches, never fewer
        due to post-scan drops. ``None`` means unfiltered (the pre-#32
        behavior). Raises ``DbDimensionMismatchError`` when
        ``len(embedding)`` does not match the configured index width.
        """
```

- [ ] **Step 4: Implement filtered SQL in the SQLite adapter**

In `backend/src/octave/db/sqlite_adapter.py`, replace the `search_similar` body:

```python
    async def search_similar(
        self,
        connection: AsyncConnection,
        embedding: Sequence[float],
        *,
        limit: int = 10,
        kind: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> list[VectorHit]:
        target = self._dim(None)
        if len(embedding) != target:
            raise DbDimensionMismatchError(expected=target, actual=len(embedding))
        sql = (
            f"SELECT item_id, distance FROM {vector_table_name(target)} "
            "WHERE embedding MATCH :query AND k = :k"
        )
        params: dict[str, object] = {
            "query": sqlite_vec.serialize_float32(list(embedding)),
            "k": limit,
        }
        if kind is not None:
            sql += " AND kind = :kind"
            params["kind"] = kind
        if user_id is not None:
            sql += " AND user_id = :user_id"
            params["user_id"] = user_id
        if session_id is not None:
            sql += " AND session_id = :session_id"
            params["session_id"] = session_id
        sql += " ORDER BY distance"
        rows = (await connection.execute(text(sql), params)).all()
        return [VectorHit(item_id=str(row[0]), distance=float(row[1])) for row in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/db/test_sqlite_adapter.py -v`
Expected: all PASS.

- [ ] **Step 6: Full gates**

Run: `cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add backend/src/octave/db/adapter.py backend/src/octave/db/sqlite_adapter.py backend/tests/db/test_sqlite_adapter.py
git commit -m "feat(db): aux-column filters on search_similar (kind/user/session)"
```

---

### Task 4: Conformance suite — engine-neutral mirror + filter contract

Any future adapter (pgvector) must pass: store→find, remove→gone, filter visibility. No-op defaults make pgvector pass by implementing `search_similar` filters alone.

**Files:**
- Modify: `backend/tests/db/conformance.py`

- [ ] **Step 1: Extend the conformance run**

Replace the body between the existing `ensure_vector_store` block and the `aclose` block in `backend/tests/db/conformance.py`. The full new function:

```python
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
                f"INSERT INTO vec_vault_items_{dim}(item_id, embedding) "
                "VALUES (:id, :vec)"
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
```

- [ ] **Step 2: Run the suite**

Run: `cd backend && uv run pytest tests/db/test_sqlite_adapter.py::test_sqlite_adapter_passes_conformance -v`
Expected: PASS (sqlite implements the contract; the existing test uses dim=2, satisfying `dim >= 2`).

- [ ] **Step 3: Full gates**

Run: `cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/db/conformance.py
git commit -m "test(db): conformance contract for vec mirror + filter visibility"
```

---

### Task 5: `VaultStore` — writes (upsert, delete) + validation + staleness

**Files:**
- Create: `backend/src/octave/db/vault_store.py`
- Create: `backend/tests/db/test_vault_store.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/db/test_vault_store.py`:

```python
"""VaultStore write-path invariants: mirror atomicity, staleness, validation."""

import struct
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from octave.db.config import DbConfig
from octave.db.errors import DbConfigError, DbDimensionMismatchError
from octave.db.models import Base, User
from octave.db.sqlite_adapter import SqliteVecAdapter, vector_table_name
from octave.db.types import VaultKind

DIM = 4


def _unit(i: int) -> list[float]:
    return [1.0 if j == i else 0.0 for j in range(DIM)]


def _blob(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


@pytest_asyncio.fixture
async def env(tmp_path: Path) -> AsyncIterator[tuple[SqliteVecAdapter, async_sessionmaker]]:
    config = DbConfig(
        adapter="sqlite",
        url=f"sqlite+aiosqlite:///{tmp_path / 'store.db'}",
        embedding_dim=DIM,
    )
    adapter = SqliteVecAdapter(config)
    engine: AsyncEngine = adapter.make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await adapter.ensure_vector_store(conn)
    factory = adapter.make_session_factory(engine)
    async with factory() as session:
        session.add(User(id="u_1", display_name="Alice"))
        session.add(User(id="u_2", display_name="Bob"))
        await session.commit()
    yield adapter, factory
    await engine.dispose()


async def test_upsert_without_embedding_persists_row(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        item = await store.upsert(
            item_id="v_1", user_id="u_1", kind=VaultKind.PREFERENCE,
            name="ui.theme", content="The user prefers a dark UI theme.",
        )
        await session.commit()
        assert item.embedding is None
        assert item.embedding_dim is None
        assert item.embedding_model is None
        hits = await store.search(user_id="u_1", embedding=_unit(0))
        assert hits == []  # findable by CRUD, invisible to search


async def test_upsert_with_embedding_writes_cache_and_mirror(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        item = await store.upsert(
            item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
            name="greet", content="Warm greetings.",
            embedding=_unit(0), embedding_model="test-model",
        )
        await session.commit()
        assert item.embedding == _blob(_unit(0))
        assert item.embedding_model == "test-model"
        assert item.embedding_dim == DIM
        hits = await store.search(user_id="u_1", embedding=_unit(0))
        assert [hit.item.id for hit in hits] == ["v_1"]


async def test_upsert_content_change_without_embedding_drops_from_index(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        await store.upsert(
            item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
            name="greet", content="v1", embedding=_unit(0),
        )
        await store.upsert(  # content changed, no new vector -> cache invalid
            item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
            name="greet", content="v2",
        )
        await session.commit()
        item = await store.get("v_1")
        assert item is not None
        assert item.content == "v2"
        assert item.embedding is None
        hits = await store.search(user_id="u_1", embedding=_unit(0))
        assert hits == []
        vec_count = (
            await session.execute(
                text(f"SELECT count(*) FROM {vector_table_name(DIM)}")
            )
        ).scalar_one()
        assert vec_count == 0


async def test_upsert_rejects_wrong_dim_before_writing(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        with pytest.raises(DbDimensionMismatchError):
            await store.upsert(
                item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
                name="x", content="c", embedding=[0.5] * 3,
            )
        assert await store.get("v_1") is None  # nothing written


async def test_upsert_rejects_embedding_model_without_embedding(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        with pytest.raises(DbConfigError):
            await store.upsert(
                item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
                name="x", content="c", embedding_model="test-model",
            )


async def test_upsert_rejects_bad_kind_and_non_mapping_meta(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        with pytest.raises(ValueError):
            await store.upsert(
                item_id="v_1", user_id="u_1", kind="agent_state",  # type: ignore[arg-type]
                name="x", content="c",
            )
        with pytest.raises(DbConfigError):
            await store.upsert(
                item_id="v_2", user_id="u_1", kind=VaultKind.SKILL,
                name="x", content="c", meta=["not", "a", "mapping"],  # type: ignore[arg-type]
            )


async def test_delete_removes_row_and_mirror(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        await store.upsert(
            item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
            name="x", content="c", embedding=_unit(0),
        )
        assert await store.delete("v_1") is True
        assert await store.delete("v_1") is False
        await session.commit()
        assert await store.get("v_1") is None
        hits = await store.search(user_id="u_1", embedding=_unit(0))
        assert hits == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/db/test_vault_store.py -v`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'octave.db.vault_store'`.

- [ ] **Step 3: Write the implementation (writes only; reads/search land in Task 6)**

Create `backend/src/octave/db/vault_store.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/db/test_vault_store.py -v`
Expected: all PASS except `test_upsert_without_embedding_persists_row` and `test_upsert_with_embedding_writes_cache_and_mirror`, which call `store.search`/`store.get` (Task 6). To keep this task green, temporarily add minimal stubs at the end of `VaultStore`:

```python
    async def get(self, item_id: str) -> VaultItem | None:
        return await self._session.get(VaultItem, item_id)
```

and skip the two `search`-calling tests via `pytest.skip("search: Task 6")` placed at their search lines — **delete both skips in Task 6 Step 3 when `search` is implemented**.

- [ ] **Step 5: Full gates**

Run: `cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/src/octave/db/vault_store.py backend/tests/db/test_vault_store.py
git commit -m "feat(db): VaultStore writes — mirror pairing, staleness, validation"
```

---

### Task 6: `VaultStore` — reads + `search` + `VaultHit`

**Files:**
- Modify: `backend/src/octave/db/vault_store.py`
- Modify: `backend/tests/db/test_vault_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/db/test_vault_store.py`:

```python
async def test_search_returns_hits_in_distance_order_with_user_scoping(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultHit, VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        await store.upsert(
            item_id="v_far", user_id="u_1", kind=VaultKind.SKILL,
            name="far", content="far text", embedding=_unit(1),
        )
        await store.upsert(
            item_id="v_near", user_id="u_1", kind=VaultKind.SKILL,
            name="near", content="near text", embedding=_unit(0),
        )
        await store.upsert(
            item_id="v_bob", user_id="u_2", kind=VaultKind.SKILL,
            name="bob", content="bob text", embedding=_unit(0),
        )
        await session.commit()
        hits = await store.search(user_id="u_1", embedding=_unit(0), limit=10)
        assert isinstance(hits[0], VaultHit)
        assert [hit.item.id for hit in hits] == ["v_near", "v_far"]
        assert hits[0].distance <= hits[1].distance
        bob_hits = await store.search(user_id="u_2", embedding=_unit(0))
        assert [hit.item.id for hit in bob_hits] == ["v_bob"]


async def test_search_scoped_by_kind_and_session_id(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        await store.upsert(
            item_id="r_1", user_id="u_1", kind=VaultKind.RUN_RECORD,
            name="chunk", content="verbatim",
            meta={"session_id": "sess_A"}, embedding=_unit(0),
        )
        await store.upsert(
            item_id="r_2", user_id="u_1", kind=VaultKind.RUN_RECORD,
            name="chunk", content="other run",
            meta={"session_id": "sess_B"}, embedding=_unit(0),
        )
        await session.commit()
        hits = await store.search(
            user_id="u_1", embedding=_unit(0),
            kind=VaultKind.RUN_RECORD, session_id="sess_A",
        )
        assert [hit.item.id for hit in hits] == ["r_1"]


async def test_list_items_filters_and_pages(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        for i in range(3):
            await store.upsert(
                item_id=f"p_{i}", user_id="u_1", kind=VaultKind.PREFERENCE,
                name=f"k{i}", content=f"c{i}",
            )
        await store.upsert(
            item_id="s_9", user_id="u_1", kind=VaultKind.SKILL,
            name="s", content="c",
        )
        await session.commit()
        prefs = await store.list_items(user_id="u_1", kind=VaultKind.PREFERENCE)
        assert {item.id for item in prefs} == {"p_0", "p_1", "p_2"}
        page = await store.list_items(user_id="u_1", limit=2, offset=0)
        assert len(page) == 2
        all_items = await store.list_items(user_id="u_1")
        assert len(all_items) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/db/test_vault_store.py -v`
Expected: FAIL — `AttributeError` for `search` / `list_items`; the two Task-5 skips fail with their skip reasons.

- [ ] **Step 3: Implement**

In `backend/src/octave/db/vault_store.py`:

3a. Remove the two `pytest.skip("search: Task 6")` lines from the Task-5 tests and the temporary `get` stub's comment marker; `get` stays as real API.

3b. Extend imports:

```python
from dataclasses import dataclass

from sqlalchemy import select
```

3c. Add `VaultHit` after `_serialize_float32_le`:

```python
@dataclass(frozen=True)
class VaultHit:
    """One vault item returned by ``VaultStore.search``, nearest first."""

    item: VaultItem
    distance: float
    """vec0 cosine distance: 0 identical, 2 opposite."""
```

3d. Update `__all__` to `["VaultHit", "VaultStore"]`.

3e. Add methods to `VaultStore` (after `delete`):

```python
    async def get(self, item_id: str) -> VaultItem | None:
        """Load one item by id, or None."""
        return await self._session.get(VaultItem, item_id)

    async def list_items(
        self,
        *,
        user_id: str,
        kind: VaultKind | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[VaultItem]:
        """Items by owner (optionally by kind), oldest first, paged."""
        stmt = select(VaultItem).where(VaultItem.user_id == user_id)
        if kind is not None:
            stmt = stmt.where(VaultItem.kind == str(VaultKind(kind)))
        stmt = (
            stmt.order_by(VaultItem.created_at, VaultItem.id)
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def search(
        self,
        *,
        user_id: str,
        embedding: Sequence[float],
        kind: VaultKind | None = None,
        session_id: str | None = None,
        limit: int = 10,
    ) -> list[VaultHit]:
        """Filtered vector search, user-scoped, nearest first.

        ``user_id`` is enforced twice — as an adapter filter and re-checked
        when rows load (defense in depth against index/table drift). A vec
        hit whose row is gone is skipped.
        """
        if limit < 1:
            raise DbConfigError("limit must be >= 1")
        connection = await self._session.connection()
        hits = await self._adapter.search_similar(
            connection,
            embedding,
            limit=limit,
            kind=str(VaultKind(kind)) if kind is not None else None,
            user_id=user_id,
            session_id=session_id,
        )
        if not hits:
            return []
        ids = [hit.item_id for hit in hits]
        rows = (
            await self._session.execute(
                select(VaultItem).where(
                    VaultItem.id.in_(ids), VaultItem.user_id == user_id
                )
            )
        ).scalars().all()
        by_id = {row.id: row for row in rows}
        return [
            VaultHit(item=by_id[hit.item_id], distance=hit.distance)
            for hit in hits
            if hit.item_id in by_id
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/db/test_vault_store.py -v`
Expected: all PASS (including the two formerly-skipped tests).

- [ ] **Step 5: Full gates**

Run: `cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/src/octave/db/vault_store.py backend/tests/db/test_vault_store.py
git commit -m "feat(db): VaultStore reads — VaultHit search, list_items, get"
```

---

### Task 7: Invariant hardening — rollback atomicity + package exports

The atomicity claim (row + mirror commit/rollback together) gets its test; public vocabulary gets exported.

**Files:**
- Modify: `backend/tests/db/test_vault_store.py`
- Modify: `backend/src/octave/db/__init__.py`

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/db/test_vault_store.py`:

```python
async def test_rollback_leaves_vec_table_and_row_clean(
    env: tuple[SqliteVecAdapter, async_sessionmaker]
) -> None:
    """Row + mirror share the session transaction: rollback undoes BOTH."""
    adapter, factory = env
    from octave.db.vault_store import VaultStore

    async with factory() as session:
        store = VaultStore(adapter, session)
        await store.upsert(
            item_id="v_1", user_id="u_1", kind=VaultKind.SKILL,
            name="x", content="c", embedding=_unit(0),
        )
        await session.rollback()

    async with factory() as session:
        store = VaultStore(adapter, session)
        assert await store.get("v_1") is None
        vec_count = (
            await session.execute(
                text(f"SELECT count(*) FROM {vector_table_name(DIM)}")
            )
        ).scalar_one()
        assert vec_count == 0
```

- [ ] **Step 2: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/db/test_vault_store.py::test_rollback_leaves_vec_table_and_row_clean -v`
Expected: PASS — the design guarantees it because `store_vector` rides `session.connection()`. If it FAILS, the bug is real (mirror escaped the transaction): do not weaken the test.

- [ ] **Step 3: Export the public vocabulary**

In `backend/src/octave/db/__init__.py`, add the import after the `types` import line:

```python
from octave.db.vault_store import VaultHit, VaultStore
```

and in `__all__`, insert `"VaultHit",` immediately after `"VectorHit",` and `"VaultStore",` immediately after `"VaultHit",` (alphabetical order maintained).

- [ ] **Step 4: Verify the public surface**

Run: `cd backend && uv run python -c "from octave.db import VaultStore, VaultHit; print('ok')"`
Expected: `ok`.

- [ ] **Step 5: Full gates**

Run: `cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/db/test_vault_store.py backend/src/octave/db/__init__.py
git commit -m "test(db): rollback atomicity for row+mirror; export VaultStore"
```

---

### Task 8: ADR + TODO bookkeeping

**Files:**
- Modify: `.agents/memory/decisions.md` (append at end of Decisions section)
- Modify: `docs/TODO.md:91`

- [ ] **Step 1: Append the ADR**

Append to `.agents/memory/decisions.md` (same bold-field structure as every other entry):

```markdown
### 2026-09-21 — Vector Mirror Behind the Adapter Seam; Aux-Column Filters; Caller-Supplied Embeddings

**Context:** Issue #32 ships the vault storage layer. vec0 is a virtual table that owns its data — unlike pgvector there is no index-on-a-column, so ANN search requires a second store (`vec_vault_items_<N>`) written through the `sqlite_vec` serializer, which is quarantined to `sqlite_adapter.py`. The 2026-09-20 data model requires Tier-2 search scoped by kind/session_id; vec0 applies filters pre-`k` only via auxiliary columns, and vec0 cannot `ALTER ADD COLUMN` — a one-way door that must be opened while the table is still disposable.

**Options Considered:** 1) consumers pair ORM writes with raw vec SQL (distributes the dual-write invariant across callers; breaks the quarantine); 2) repository emits vec SQL directly (breaks the quarantine); 3) `store_vector`/`remove_vector` on `DbAdapter` with no-op defaults, pairing owned by one `VaultStore`, aux columns added to the DDL now, embeddings caller-supplied.

**Decision:** Option 3. `VaultStore` (in `octave.db.vault_store`) owns the transaction pairing, the staleness rule (new content without a new vector NULLs the cache and drops the vec row), and `VaultKind`/`meta` validation; it never commits. `search_similar` gains keyword-only `kind`/`user_id`/`session_id` filters applied pre-`k` via aux columns (`session_id` denormalized from `meta.session_id`). Embeddings are caller-supplied — `octave.db` never imports `octave.inference`; the Context Manager service layer composes `embed → upsert` and owns the model-selection policy.

**Rationale:** The mirror is a SQLite-ism (pgvector's column *is* the index host), so the adapter methods default to no-ops and the conformance suite pins only the observable contract: store→findable, remove→gone, filters visible. The DDL change is free now and a full re-embed later; doing it later would strand filtered search in #11 behind a data migration.

**Consequences:** Writes must go through `VaultStore`, never raw ORM mutations of `vault_items` (bypasses desync the index). `upsert` is full replacement, not patch — read-modify-write to preserve an embedding. Dim-change repair = `upsert` with fresh vectors; no auto-re-embed ships (detection columns + idempotent upsert are the primitives). Reopened if tag-filtered search arrives: tags live in `meta` JSON with no `vault_tags` table, so that item repeats this one-way-door analysis.
```

- [ ] **Step 2: Mark TODO #2 done**

In `docs/TODO.md`, replace line 91:

```markdown
- [ ] 2. Implement vector-capable storage layer for context vault (CRUD operations, queries, vector indexing)
```

with:

```markdown
- [x] 2. Implement vector-capable storage layer for context vault (CRUD operations, queries, vector indexing) — PR #97
```

- [ ] **Step 3: Verify ADR format consistency**

Confirm the new entry uses `Context / Options Considered / Decision / Rationale / Consequences` bold fields like every other entry.

- [ ] **Step 4: Commit**

```bash
git add .agents/memory/decisions.md docs/TODO.md
git commit -m "docs(memory): ADR for vector mirror seam; close CM TODO #2"
```

---

### Task 9: Final verification & PR

- [ ] **Step 1: Full gates from a clean state**

Run: `cd backend && uv run pytest -q && uv run ruff check src tests && uv run mypy src`
Expected: all green.

- [ ] **Step 2: Confirm the diff matches the deliverables list**

Run: `git log --oneline main..HEAD`
Expected commits: design doc; vec seam (DDL + store/remove); filtered search; conformance; VaultStore writes; VaultStore reads; atomicity + exports; ADR/TODO. No Alembic migration files, no model changes, no `octave.inference` imports in `octave.db` (`grep -rn "octave.inference" backend/src/octave/db/` → no output).

- [ ] **Step 3: Push and update the PR**

```bash
git push
```

Update PR #97's body to link the spec (`.agents/specs/2026-09-21-vector-capable-storage-layer-design.md`) and this plan, then mark it ready for review.
