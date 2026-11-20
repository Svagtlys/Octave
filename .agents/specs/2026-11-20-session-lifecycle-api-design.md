# Session Lifecycle API — Design (issue #121)

**Date:** 2026-11-20
**Branch:** `feature/session-create-list-api` · **PR:** https://github.com/Svagtlys/Octave/pull/147
**Goal:** Ship the session create/list HTTP surface: `POST /api/sessions` creates a
Session (status `active`, `created_by_user_id` = authenticated user) plus the owner
`session_participants` row in one transaction (optional title; 201). `GET /api/sessions`
returns the caller's sessions only, ordered most-recently-active first, with
limit/offset pagination and a last-message preview.

## Context

The transcript schema (`sessions`, `session_participants`, `events`) shipped in the
2026-09-13 ADR; the write paths (`EventStore`) and the composition conventions
(`get_db_session`, `get_current_user`, plane-owned stores that never commit,
middleware error mapping) shipped with auth (#122) and the agent/context planes.
This item is the first *user-facing session surface* — the seam the chat UI (#7)
and Integration composition build on.

Key facts the design leans on:

- `AuthStore.create_user` creates a `Participant` row for every user (unique
  constraint `uq_participants_user`); the auth migration backfilled existing users.
  A user without a Participant row is a data-integrity bug, not a normal state.
- `Session.created_by_user_id` is the single owner (2026-09-13 Decision 2);
  participation is N via `session_participants`.
- `EventStore` owns transcript appends; this item never appends events — it only
  reads them (for ordering and preview).

## Decisions

| # | Decision | Alternatives rejected |
|---|----------|-----------------------|
| 1 | **`SessionStore` in `octave/db/session_store.py`** — plane-owned store over a caller-supplied `AsyncSession`, never commits (the `EventStore`/`VaultStore`/`McpConfigStore` contract). Routes own commits via `get_db_session`. | Route-level SQLAlchemy (untestable SQL, diverges from every sibling store); JOIN/GROUP BY listing (GROUP BY must enumerate every selected column; preview needs a second query anyway) |
| 2 | **Correlated scalar subqueries** for `last_activity` (`COALESCE(MAX(events.created_at), sessions.created_at)`) and `last_message` (latest message-kind event by `seq`). No `GROUP BY`, so `ORDER BY`/`LIMIT`/`OFFSET` stay one clean statement; the preview subquery rides the unique index `(session_id, seq)`. | LEFT JOIN + GROUP BY + batch preview (more machinery, no measurable gain at local-first scale); denormalized `last_activity_at` column (schema change; derivable from the transcript, which is the source of truth) |
| 3 | **Membership-scoped listing** (`session_participants` join, `left_at IS NULL`) rather than ownership (`created_by_user_id`). Today the sets are identical (POST only creates owner-membership); membership is the future-proof axis for shared sessions. | Ownership filter (matches the ADR's "listing" wording but re-litigates later when sharing arrives) |
| 4 | **Preview counts only `user_message`/`assistant_message`**, selected as the latest such event by `seq`; `tool_call`/`tool_result`/`system`/`context_injection` never produce a preview. Plain truncated content string (140 chars, ellipsis), `null` when the session has no message events. Truncation happens at the route boundary (presentation), not in the store. | Any-kind preview (`[tool call]` placeholders — noise for a session list); structured `{kind, author, content}` (no consumer needs it yet) |
| 5 | **Fail loud on missing owner Participant.** `OwnerParticipantMissingError(DbError)` → middleware maps `DbError` → 500 JSON. Never self-heal by creating the row. (`Error` suffix per the db-plane convention enforced by ruff N818 — the auth/mcp/context planes carry explicit per-file-ignores for their spec-pinned names; the db plane does not.) | Self-healing insert (papers over a backfill bug); ignoring the case (silent 500 from a raw NOT NULL) |
| 6 | **Response envelope** `{items, total, limit, offset}` where `total` counts the caller's current memberships. Session object = exactly `id, title, created_at, last_activity, last_message` (POST returns `last_activity = created_at`, `last_message = null`). | Plain array (client guesses end-of-list); envelope without `total` (UI needs page counts) |
| 7 | **Pagination via query params** `limit` (default 20, `ge=1 le=100`) and `offset` (default 0, `ge=0`); violations → 422 from FastAPI validation. Offset past the end → empty `items` with real `total`. All session statuses listed (no status filter). | Cursor pagination (no consumer; offset suffices for a personal-scale list) |

## Architecture

```
POST /api/sessions                          GET /api/sessions
        |                                          |
get_current_user (cookie -> User) <---------+------+
        |                                          |
routes/sessions.py                            routes/sessions.py
  - resolve Participant (fail loud)             - resolve Participant (fail loud)
  - SessionStore.create_session                 - SessionStore.list_sessions(limit, offset)
  - session.commit()  <-- route owns commit     - map rows -> SessionResponse (truncate preview)
        |                                          |
   201 SessionResponse                        200 SessionListResponse
```

Errors: `AuthError` mapping (existing), `DbError` → 500 (new handler),
FastAPI 422 for body/query validation, 401 from `get_current_user`.

## Components

### `octave/db/session_store.py` (new)

```python
@dataclass(frozen=True)
class SessionActivityRow:
    id: str
    title: str | None
    created_at: datetime
    last_activity: datetime
    last_message: str | None  # raw (untruncated) content of the latest message event


class SessionStore:
    """Session lifecycle queries. Never commits (get_db_session owns the boundary)."""

    def __init__(self, session: AsyncSession) -> None: ...

    async def get_participant_for_user(self, user_id: str) -> Participant | None:
        """The user's speaker identity (Participant.user_id == user_id)."""

    async def create_session(
        self, *, owner_user_id: str, owner_participant_id: str, title: str | None
    ) -> Session:
        """Insert Session (status active, id uuid4-hex) + owner
        SessionParticipant (role speaker) in one flush. Never commits."""

    async def list_sessions(
        self, *, participant_id: str, limit: int, offset: int
    ) -> tuple[list[SessionActivityRow], int]:
        """Membership-scoped page, most-recently-active first
        (COALESCE(max event created_at, created_at), tie-break id DESC),
        plus the caller's total membership count."""
```

Listing SQL (as built by SQLAlchemy `select`):

```sql
SELECT sessions.id, sessions.title, sessions.created_at,
       COALESCE((SELECT MAX(e.created_at) FROM events e
                 WHERE e.session_id = sessions.id),
                sessions.created_at)                          AS last_activity,
       (SELECT m.payload['content'] FROM events m
        WHERE m.session_id = sessions.id
          AND m.kind IN ('user_message', 'assistant_message')
        ORDER BY m.seq DESC LIMIT 1)                          AS last_message
FROM sessions
JOIN session_participants sp
  ON sp.session_id = sessions.id
 AND sp.participant_id = :participant_id
 AND sp.left_at IS NULL
ORDER BY last_activity DESC, sessions.id DESC
LIMIT :limit OFFSET :offset
```

`total`: `SELECT COUNT(*) FROM session_participants sp JOIN sessions ... WHERE
sp.participant_id = :pid AND sp.left_at IS NULL`.

Message kinds filter uses `EventKind.USER_MESSAGE`/`ASSISTANT_MESSAGE` values
(the enum stays the source of truth; TEXT column, app-validated per the
2026-09-13 ADR).

### `octave/routes/sessions.py` (new)

- `router = APIRouter(prefix="/sessions", tags=["sessions"])`; mounted in `app.py`
  under `/api` alongside the auth router.
- Pydantic models:
  - `CreateSessionRequest`: `title: str | None = Field(default=None, max_length=200)`
  - `SessionResponse`: `id, title, created_at, last_activity, last_message`
  - `SessionListResponse`: `items: list[SessionResponse], total, limit, offset`
- `POST ""` (201): `Depends(get_current_user)` → `get_participant_for_user`
  (`None` → `raise OwnerParticipantMissingError(user.id)`) → `create_session` →
  `await session.commit()` → `SessionResponse` with `last_activity = created_at`,
  `last_message = None`.
- `GET ""`: `Depends(get_current_user)` → participant lookup (same fail-loud) →
  `list_sessions` → map rows, truncating preview to 140 chars (append `…` on
  truncation) → envelope. `limit: int = Query(20, ge=1, le=100)`,
  `offset: int = Query(0, ge=0)`.
- The regression gate `test_every_non_public_route_requires_identity` covers the
  new routes automatically (both depend on `get_current_user`).

### `octave/db/errors.py` (edit)

```python
class OwnerParticipantMissingError(DbError):
    """A user has no Participant row — invariant broken (AuthStore.create_user
    and the auth backfill migration guarantee one per user). Fail loud; never
    self-heal by inserting a Participant on the fly."""
```

### `octave/middleware.py` (edit)

`@app.exception_handler(DbError)` → `JSONResponse(500, {"error": type(exc).__name__, "detail": str(exc)})` —
same single-map-point shape as the `AuthError` handler. No route raises
`HTTPException` for domain failures.

### `octave/db/__init__.py` (edit)

Export `SessionStore`, `SessionActivityRow`, `OwnerParticipantMissingError` (from
`octave.db.errors`).

## Error handling matrix

| Condition | Result |
|---|---|
| No/invalid/expired cookie | 401 (`get_current_user`) |
| Body validation (title > 200 chars) | 422 (FastAPI) |
| `limit`/`offset` out of bounds (`limit=0`, `limit=101`, `offset=-1`) | 422 (FastAPI `Query` constraints) |
| Caller has no Participant row (data bug) | 500 `OwnerParticipantMissingError` (middleware `DbError` handler) |
| Factory unconfigured | 503 (`get_db_session`, existing) |

## Testing

Real SQLite via the shared `engine`/`session_factory` fixtures — no mocks
(coding rules: integration tests for all new endpoints).

**`tests/db/test_session_store.py`:**

- `create_session` inserts session (`status="active"`, uuid4-hex id) + owner
  membership (`role="speaker"`) atomically; optional title NULL.
- `get_participant_for_user`: hit and miss.
- Ordering: session with events sorts by `MAX(events.created_at)`; eventless
  session falls back to `created_at`; mixed list interleaves correctly;
  deterministic tie-break by `id DESC`.
- Preview: latest `user_message`/`assistant_message` by `seq` wins regardless of
  insertion order into the query; `tool_call`/`system`/`context_injection`
  ignored; eventless or message-less-but-eventful session → `last_message=None`.
- Scoping: another user's sessions invisible; `left_at` set → excluded; `total`
  counts all current memberships regardless of page slice.
- Pagination: limit/offset slices; offset past end → empty list, real total.
- Never-commits contract: uncommitted create invisible to a second session.

**`tests/routes/test_sessions.py`** (app assembled like `test_auth_routes.py`:
real SQLite file, `DummyHasher`, auth + sessions routers, `add_error_handlers`):

- POST without cookie → 401.
- POST 201: response has id/title/created_at; DB shows session (`active`, owner
  = caller) + owner membership (`speaker`); optional title omitted → null.
- POST title > 200 chars → 422.
- GET returns only the caller's sessions (bootstrap owner Alice, admin-create
  Bob, login as Bob: Bob sees only his).
- GET ordering: seed events via `EventStore` (with participant rows) —
  most-recently-active first; eventless session ordered by `created_at`.
- GET preview: content present; > 140 chars → truncated with ellipsis;
  message-less session → null.
- GET envelope: `{items, total, limit, offset}`; defaults 20/0; page 2 of 3
  sessions returns the oldest; `limit=0`/`limit=101`/`offset=-1` → 422.
- Fail-loud: delete the caller's Participant row via raw SQL → POST → 500 with
  `error == "OwnerParticipantMissingError"`.
- Regression: existing `test_every_non_public_route_requires_identity` passes
  with the router mounted on the global app.

**Gates** (from `backend/`): `uv run pytest -q && uv run ruff check src tests &&
uv run mypy src` — mypy strict clean.

## Out of scope

- `PATCH`/`DELETE` /api/sessions/{id}; status transitions (`completed`/`failed`/
  `cancelled`); rename; archiving.
- Sending messages / `POST /api/sessions/{id}/messages` (Integration #1 owns the
  router-backed write path; this item reads events only).
- Session detail endpoint, participants management, agent spawning into sessions.
- `sessions.updated_at` as the activity clock — rejected: `updated_at` tracks row
  edits, not transcript activity; the transcript is the activity source of truth.
- Frontend wiring (chat UI #7 consumes this API later).

## Consequences

- The chat UI and Integration composition get a stable session-list contract;
  the preview/order semantics are pinned by store tests, so later work items
  cannot silently change them.
- `DbError` → 500 becomes the second plane-level middleware mapping; future
  db-plane invariant errors ride the same handler.
- Membership-scoped listing means a future "share session" work item adds
  visibility with zero query change.
