# Context Vault — Vector-Capable Storage Layer Design

**Work item:** #32 — feat(context): implement vector-capable storage layer
**Branch:** `feature/vector-capable-storage-layer` · **Draft PR:** [#97](https://github.com/Svagtlys/Octave/pull/97)
**Date:** 2026-09-21
**Status:** Approved in brainstorming 2026-09-21

**Scope summary:** ship the vault storage layer on the existing `octave.db` adapter seam: a `VaultStore` (upsert / delete / get / list_items / search) enforcing the write-path invariants — ORM row + vec mirror atomicity, embedding-cache staleness, `VaultKind`/`meta` validation — plus `DbAdapter` extensions (`store_vector` / `remove_vector` with no-op defaults, keyword-only `kind`/`user_id`/`session_id` filters on `search_similar`) and the vec0 aux-column DDL that makes filtered ANN search exact. Embeddings are **caller-supplied**: `octave.db` never imports `octave.inference`. No migration, no new tables, no HTTP routes, no re-embed job.

**Depends on:** #31 ([`vault_items` data model + `VaultKind`](./2026-09-20-context-vault-data-model-design.md), PR #96) and the shipped adapter seam (PR #84, [`DbAdapter`](../../backend/src/octave/db/adapter.py), [`SqliteVecAdapter`](../../backend/src/octave/db/sqlite_adapter.py)).

---

## Context

CM TODO #2: "Implement vector-capable storage layer for context vault (CRUD operations, queries, vector indexing)". The schema and adapter seam already exist: `vault_items` (content = source of truth, `embedding` = float32 BLOB cache, `embedding_model`/`embedding_dim` = staleness detection), and the adapter-private dim-suffixed `vec_vault_items_<N>` vec0 virtual table created by `ensure_vector_store` at `OCTAVE_DB_EMBEDDING_DIM`.

The [`DbAdapter` docstring](../../backend/src/octave/db/adapter.py) records the boundary this design honors: *per-table CRUD is engine-neutral SQLAlchemy ORM work and stays OUT of the adapter interface*. The adapter owns engine/session lifecycle, vector-store DDL, and similarity search. This item therefore splits cleanly: CRUD + policy live in a new store module; vec mechanics (which are quarantined to `sqlite_adapter.py` by the `sqlite_vec` import rule) gain named adapter methods the store calls.

The [vault data-model design](./2026-09-20-context-vault-data-model-design.md) (Decision 4) recorded a hard requirement inherited by this item: Tier-2 run search needs vector search **scoped by kind and `session_id`**, and the shipped `search_similar` has no filter parameter. This design discharges that requirement.

### Why a store module at all

Plain CRUD needs no repository — `session.get(VaultItem, id)` and `select().where()` are already interfaces, and wrapping SQLAlchemy generically re-abstracts it. What justifies a module is three write-path invariants nothing else enforces:

1. **Dual-write atomicity.** Every insert/update/delete of a `vault_items` row must be mirrored into `vec_vault_items_<N>`, and the vec write cannot be raw engine-neutral SQL (`sqlite_vec.serialize_float32` is quarantined to `sqlite_adapter.py`). If each consumer hand-rolls the ORM-write/vec-write pairing, one omission silently desyncs the index.
2. **Embedding-cache staleness rules.** Content changed without a new vector → cache is invalid: NULL the columns, drop the vec row. Width mismatch → hard error. These are policy, not plumbing.
3. **Write-time validation.** `VaultKind` guard on `kind`; `meta` must be a JSON object (keys unrestricted, per the thin-envelope `extra="allow"` rule).

The module is a write-path invariant keeper that also offers read conveniences — a store, not a classic repository.

---

## Decision 1 — Embeddings are caller-supplied

`VaultStore` accepts `embedding: Sequence[float] | None` + `embedding_model: str | None` on `upsert` and **never generates vectors**. `octave.db` does not import `octave.inference`.

- The Context Manager's later service layer (CM TODO #3/#5) composes `InferenceAdapter.embed()` → `VaultStore.upsert(..., embedding=vec, embedding_model=name)`; the config-driven "use model X, always embed kinds Y" policy lives there, not here.
- `embedding_model`/`embedding_dim` columns (shipped) record which model produced each cached vector, so a model swap is detectable and re-embeddable.
- Benefit: the db test suite needs no inference fakes; the seam stays swappable in both directions.

Rejected: injected embedder Protocol inside `octave.db` (an abstraction seam before a second consumer exists); direct inference import (crosses the package boundary).

## Decision 2 — Filters extend `DbAdapter.search_similar`; aux columns in the vec0 DDL

```python
async def search_similar(
    self, connection, embedding, *,
    limit: int = 10,
    kind: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
) -> list[VectorHit]: ...
```

All filters keyword-only, `None` = unfiltered = today's exact behavior (existing call sites and conformance assertions untouched).

**Why now, not in #11 (the query-interface item):**

1. **The DDL is a one-way door.** Exact filtered recall in vec0 requires filter fields as **auxiliary columns**, so vec0 applies them *before* `k` (`WHERE embedding MATCH :q AND k = :k AND kind = :kind`). A `JOIN vault_items` after the scan filters *after* `k` — silently ≤ k hits. vec0 cannot `ALTER ADD COLUMN`, so adding aux columns later means drop + rebuild + re-embed every vault item — and re-embedding needs an embedding pipeline that doesn't exist yet. This item already owns vec DDL ("vec index maintenance"); deferring hands #11 a hidden data migration.
2. **The contract hardens now.** `DbAdapter` + `conformance.py` are the frozen contract future engines (pgvector) implement. An optional keyword is one signature line today; retrofitted later it is a contract break for an adapter possibly already in flight.
3. **Separation.** #11 is query UX (relevance scoring, token budgets, cross-corpus ranking). A filtered KNN primitive is storage plumbing.

**Scope guard:** exactly three filters (`kind`, `user_id`, `session_id`), all optional. Not a query language.

Rejected: over-fetch-and-filter in the app layer (silent recall degradation dressed as SQL); defer everything to #11 (DDL one-way door).

## Decision 3 — The vec mirror hides behind named adapter methods; `VaultStore` owns the pairing

`DbAdapter` gains two methods with **non-abstract no-op defaults** (mirroring `aclose`):

```python
async def store_vector(
    self, connection, *, item_id: str, embedding: Sequence[float],
    kind: str | None = None, user_id: str | None = None,
    session_id: str | None = None,
) -> None: ...

async def remove_vector(self, connection, *, item_id: str) -> None: ...
```

- **Why methods exist at all:** vec0 is not an index — it is a *virtual table that owns its data* (internal shadow tables, reachable only via `INSERT`/`MATCH`). There is no "create index on vault_items.embedding" equivalent; the ANN capability requires a second store, and writing it requires the quarantined serializer. The three-way split stands: `content` = truth, `embedding` BLOB = engine-neutral cache (survives vec drops, migration-expressible, ORM-readable), `vec_vault_items_<N>` = derived ANN index (disposable; `ensure_vector_store` already drops/recreates on dim change).
- **Why no-op defaults:** the mirror is a SQLite-ism. On pgvector the column *is* the index host; a future adapter inherits silence and implements only `search_similar` (natively filtered). The conformance contract is engine-neutral: *after `store_vector`, `search_similar` finds the item; after `remove_vector`, it does not* — pgvector passes by doing nothing on the write side.
- `SqliteVecAdapter` overrides both with vec0 `INSERT OR REPLACE` / `DELETE`, wrapping vendor errors at the boundary (never leak `sqlite3`/vec0 exceptions).

**Vec0 DDL change** in `ensure_vector_store`:

```sql
CREATE VIRTUAL TABLE vec_vault_items_<N> USING vec0(
  item_id TEXT PRIMARY KEY,
  embedding float[<N>] distance_metric=cosine,
  kind TEXT,
  user_id TEXT,
  session_id TEXT
)
```

`session_id` is denormalized from `meta.session_id` into the vec row — the one accepted duplication: vec0 cannot filter a joined JSON column pre-`k`, and run items require `meta.session_id` (data-model Decision 4). The store extracts it at write time. `kind`/`user_id` are always present on every item, so the store always populates all three aux fields; they are nullable in DDL only for robustness. Idempotence/staleness behavior of `ensure_vector_store` is otherwise unchanged (dim change = drop + recreate + WARN).

**Quarantine rule holds:** only `sqlite_adapter.py` imports `sqlite_vec`; `vault_store.py` is pure SQLAlchemy + adapter calls.

## Decision 4 — `VaultStore` API

New module [`backend/src/octave/db/vault_store.py`](../../backend/src/octave/db/vault_store.py). Constructed with `(adapter: DbAdapter, session: AsyncSession)`. **Never commits** — callers own transaction boundaries (the `get_db_session` rule). The vec mirror rides the caller's session connection (`session.connection()`), so ORM flush + vec write are one atomic transaction.

```python
@dataclass(frozen=True)
class VaultHit:
    item: VaultItem
    distance: float
    """vec0 cosine distance: 0 identical, 2 opposite."""


class VaultStore:
    def __init__(self, adapter: DbAdapter, session: AsyncSession) -> None: ...

    async def upsert(
        self, *,
        item_id: str,                       # app-generated uuid4 hex, like other tables
        user_id: str,
        kind: VaultKind,                    # enum, not str — app-level validation
        name: str,
        content: str,
        meta: Mapping[str, Any] | None = None,   # JSON object; keys unrestricted
        embedding: Sequence[float] | None = None,
        embedding_model: str | None = None,
    ) -> VaultItem: ...

    async def delete(self, item_id: str) -> bool: ...          # False if absent

    async def get(self, item_id: str) -> VaultItem | None: ...

    async def list_items(
        self, *, user_id: str, kind: VaultKind | None = None,
        limit: int = 100, offset: int = 0,
    ) -> list[VaultItem]: ...

    async def search(
        self, *,
        user_id: str,                       # mandatory: search is always user-scoped
        embedding: Sequence[float],
        kind: VaultKind | None = None,
        session_id: str | None = None,
        limit: int = 10,
    ) -> list[VaultHit]: ...
```

**`upsert` semantics (the invariant core):**

- Validation: `kind` is typed `VaultKind`; `meta` must be a mapping (non-dict raises `DbConfigError`); `len(embedding) != config.embedding_dim` raises `DbDimensionMismatchError` **before any write**; `embedding_model` without `embedding` raises `DbConfigError` (incoherent write).
- Embedding supplied → write BLOB + `embedding_model` + `embedding_dim` columns, then `adapter.store_vector(...)` with aux fields (`kind`, `user_id`, `session_id = meta.get("session_id")`).
- No embedding supplied → embedding columns NULLed, `adapter.remove_vector(...)`. Covers both "new item not yet embedded" and "content changed, vector stale": an unembedded item is findable by CRUD, invisible to search. `embedding` is a cache of `content`; content changed = cache invalid.
- **Upsert is full replacement, not patch.** Every call states the complete desired field set; omitted optionals (`meta=None` → `{}`, `embedding=None` → no embedding) are written as such. A caller wanting "keep existing embedding, change name" must read-modify-write (re-supply the current vector). This keeps the primitive idempotent and the invariant rules unconditional — no "was the column meant unchanged?" ambiguity.
- Timestamps via existing column defaults (`utcnow`, `onupdate`).
- One idempotent primitive (no create/update split): the vault builder (CM #3) and run archival (CM #5) both write idempotently by known id; the future re-embed path is the same call.

**`search` semantics:** calls `adapter.search_similar(...)` for `(item_id, distance)` pairs, then batch-loads rows (`select(VaultItem).where(VaultItem.id.in_(...))`) preserving hit order, returning `VaultHit(item, distance)`. `user_id` is enforced twice: as an adapter filter *and* re-checked in the row load (defense in depth against index/table drift). A vec hit whose row is gone is skipped (should not happen inside one transaction; cheap to tolerate).

**`delete`:** ORM row delete + `adapter.remove_vector(...)` in the same transaction; returns whether a row existed.

**`VaultHit` lives in `vault_store.py`, not `types.py`:** `types.py` is dependency-free wire vocabulary (enums, payloads); a hit carrying an ORM `VaultItem` would force a `types → models` import. Adapter-level `VectorHit` (id + distance) stays in `types.py` at the adapter layer; the two types live at their own layers.

## Decision 5 — Errors: reuse the hierarchy, no new types

- `DbDimensionMismatchError` — `upsert` width check and `search_similar` (existing). One contract: "re-embed before searching".
- `DbConfigError` — incoherent store calls (`embedding_model` without `embedding`; `limit < 1`).
- Vendor exceptions stay translated at the adapter boundary (existing `errors.py` philosophy); `store_vector`/`remove_vector` wrap vec0/`sqlite3` failures as `DbError`.
- `delete`/`get` on missing ids return `False`/`None` — absence is not exceptional.

## Decision 6 — Dim-change & staleness: detection primitives, not the repair job

`ensure_vector_store` drops a wrong-dim index at startup with WARN "re-embedding pending" (shipped behavior). After a dim change every row is detectably stale (`embedding_dim != config.embedding_dim`, or NULL). This item ships:

- the columns + `list_items` (a re-embed script filters on them);
- `upsert` with a fresh embedding **as** the repair operation (idempotent overwrite of BLOB + vec row).

Explicitly **not** shipping: auto re-embed, a `stale_embeddings()` helper, or any embedding generation. The embedding pipeline (Inference TODO #6, CM #3/#5) owns the trigger and batch script; this design records the hand-off.

---

## Testing

TDD throughout; gates `uv run pytest -q && uv run ruff check src tests && uv run mypy src` from `backend/`. The existing [`conftest.py`](../../backend/tests/db/conftest.py) temp-file engine fixture serves store tests unchanged; store tests build an adapter via `default_registry.create(DbConfig(...))`.

| File | Adds |
|---|---|
| `tests/db/conformance.py` | engine-neutral mirror contract: store→search→find; remove→search→gone; filter visibility: item stored `kind="prompt"` invisible to `kind="skill"` filter, visible to its own |
| `tests/db/test_sqlite_adapter.py` | aux-column DDL present; filtered `search_similar` exactness (≤ k true hits); `store_vector` dim mismatch raises; stale-dim drop/recreate keeps aux columns; float32 round-trip |
| `tests/db/test_vault_store.py` (new) | upsert insert/update; embedding-supplied vs NULL-ing (stale cache dropped from vec); dim mismatch raises pre-write; `VaultKind` typed; `meta` non-mapping rejected; `session_id` extracted to aux field; `search` order + user scoping + cross-user invisibility; delete removes from both stores; **rollback leaves the vec table clean too** (atomicity claim tested) |
| `tests/db/test_types.py` | unchanged for `VectorHit`; `VaultHit` stability tested in store tests |

## Deliverables

| File | Action |
|---|---|
| `backend/src/octave/db/vault_store.py` | **new** — `VaultStore`, `VaultHit` |
| `backend/src/octave/db/adapter.py` | modify — `store_vector`/`remove_vector` no-op defaults; `search_similar` filter kwargs; docstrings |
| `backend/src/octave/db/sqlite_adapter.py` | modify — aux-column DDL; vec overrides with vendor-error wrapping; filtered search SQL |
| `backend/src/octave/db/__init__.py` | modify — re-export `VaultStore` (and `VaultHit`) |
| `backend/tests/db/conformance.py` | modify — mirror + filter contract |
| `backend/tests/db/test_sqlite_adapter.py` | modify |
| `backend/tests/db/test_vault_store.py` | **new** |
| `.agents/memory/decisions.md` | modify — ADR: vec mirror behind adapter seam, aux-column filters, caller-supplied embeddings |
| `docs/TODO.md` | modify — mark CM #2 done with PR ref |

## Non-goals (this work item)

- No embedding generation or inference imports in `octave.db`; no "always embed kind X with model Y" policy service.
- No re-embed/reindex job or trigger; no `stale_embeddings()` helper.
- No per-kind Pydantic `meta` content models (skill `params`, preference `value`) — thin-envelope validation here is `VaultKind` + "meta is a JSON object"; kind-specific validation is CM #8/#9 consumer work.
- No HTTP/REST routes or new FastAPI wiring; the store is library surface (routes land with CM UI items).
- No pgvector adapter; no `events.embedding`; no multi-user vault visibility changes.
- No relevance scoring or token budgets (CM #4/#6) — `search` returns raw `(item, distance)` hits.

## Open questions (recorded, not blocking)

- If a future item needs `search` without mandatory `user_id` (admin/inspection), it is one keyword flip — but the data model treats `user_id` as the search-scoping axis, so mandatory-by-default is the safe posture.
- Aux columns are filter-only today; if tag-filtered search ever arrives (CM #4), tags are still in `meta` JSON (no `vault_tags` table) — that item reopens this DDL question with its own one-way-door analysis.
