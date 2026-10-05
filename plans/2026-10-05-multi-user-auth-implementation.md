# Multi-User Authentication — Implementation Plan

**Issue:** #122 · **Spec:** `.agents/specs/2026-10-04-multi-user-auth-design.md` (binding — this plan does not re-decide anything; it sequences the spec)
**Branch:** `feature/multi-user-auth` (already checked out; spec commit `8813cd7`)
**Process:** TDD per task — write failing test, run it, implement, run suite. `cd backend && uv run pytest -q`, `uv run ruff check .`, `uv run mypy --strict src/` must stay green. Frontend: `cd frontend && npm run test -- --run`, `npm run lint`.

## Verified preconditions (audited 2026-10-05)

- CORS in [`middleware.py`](../backend/src/octave/middleware.py) already sets `allow_credentials=True` with explicit `ALLOWED_ORIGINS` — **no change needed** (spec item pre-satisfied).
- `pydantic-settings>=2.0.0` already a dep; `argon2-cffi` is the only addition (`uv add argon2-cffi`).
- `get_db_session` in [`db/deps.py`](../backend/src/octave/db/deps.py) yields from `app.state.db_session_factory`, never commits — `get_current_user` must compose with it, not replace it.
- `client.ts` `request()` does **not** send cookies yet — add `credentials: 'include'`.
- Migrations live in `backend/src/octave/db/migrations/versions/`; head chain: `4bf075ee2ede → b7c3f1a2d9e4 → c9d4e2f6a1b8`. New migration `down_revision = "c9d4e2f6a1b8"`.
- Route mounting pattern: `app.include_router(x_router, prefix="/api")` in [`app.py`](../backend/src/octave/app.py).

---

## Phase 1 — Schema (1 commit)

### Task 1: Models + migration + backfill

**Files:** `db/models/core.py` (extend `User`), `db/models/auth.py` (new `AuthSession`), `db/models/__init__.py`, `db/types.py` (`UserRole`, `UserStatus` StrEnums), `db/migrations/versions/<rev>_auth_schema.py`, `tests/db/test_models.py`, `tests/db/test_migrations.py`

**Tests first:**
1. `User` has `username` (unique), nullable `password_hash`, `role='member'` default, `status='active'` default, nullable `last_login_at`.
2. `AuthSession` row: PK is token hash; cascade delete with user.
3. Migration test (the spec's hard one): build a DB on `c9d4e2f6a1b8` schema with seeded users/sessions/vault rows via the old models → run `alembic upgrade head` → assert: all rows survive; usernames unique (dedup `.2`, `.3` for collisions); oldest user by `created_at` has `role='owner'`; everyone `status='active'`; `password_hash` set to a random value (login impossible until reset); missing `Participant` rows backfilled.

**Implementation notes:** TEXT columns + app-level enum validation (no DB CHECK — spec decision). `AuthSession.__table__` gets `Index("ix_auth_sessions_user_id", ...)` and `Index("ix_auth_sessions_expires_at", ...)`. Backfill slug: lowercase `display_name`, non-`[a-z0-9._-]` → `-`, collapse, clamp 2–32 chars, fallback `user` for empty slugs.

**Verify:** full `pytest tests/db`, `ruff`, `mypy --strict`. **Commit:** `feat(db): auth schema — user credentials, roles, auth_sessions (+ pre-auth backfill)`

## Phase 2 — auth package core (2 commits)

### Task 2: passwords + tokens + config + errors

**Files:** `auth/__init__.py`, `auth/passwords.py`, `auth/tokens.py`, `auth/config.py`, `auth/errors.py`, `tests/auth/__init__.py`, `tests/auth/test_passwords.py`, `tests/auth/test_tokens.py`, `tests/auth/test_config.py`

**Tests first:**
1. `Argon2Hasher.hash/verify` round-trip; wrong password → `False`; `needs_rehash` false for fresh hash.
2. `hash_token`: sha256 hex, 64 chars, deterministic; `generate_token` returns ≥32 bytes urlsafe, distinct across calls.
3. `AuthSettings` (env prefix `OCTAVE_AUTH_`): `cookie_secure: bool = False`, `idle_ttl_days: int = 14`, `absolute_ttl_days: int = 90`.
4. `AuthError` hierarchy: `InvalidCredentials`, `AccountDisabled`, `SessionExpired`, `UsernameTaken`, `NotLastOwnerGuard` — each with an HTTP status mapping helper (401/401/401/409/409).

**Implementation notes:** `PasswordHasher` is a `Protocol` (`hash`, `verify`, `needs_rehash`); default impl `Argon2Hasher` wraps `argon2.PasswordHasher` (argon2id defaults). Tests inject `DummyHasher` (identity-prefix scheme) so the suite stays fast — **never** call real argon2 in non-hasher tests; fixtures use the dummy. `add argon2-cffi` via `uv add`.

**Commit:** `feat(auth): password hashing, token primitives, settings, error types`

### Task 3: AuthStore + AuthService + CLI

**Files:** `auth/store.py`, `auth/service.py`, `auth/cli.py`, `tests/auth/test_store.py`, `tests/auth/test_service.py`, `tests/auth/test_cli.py`

**Tests first (Store, real sqlite fixture from `tests/db/conftest.py`):**
1. `create_user` → row + `Participant` in same flush; duplicate username → `UsernameTaken`.
2. `get_by_username` (case-folded), `get_by_token_hash`, `touch_session` (updates `last_seen_at`, slides `expires_at` bounded by absolute cap).
3. `delete_sessions_for_user` → 0 rows after deactivate; expiry filter: `get_by_token_hash` returns `None` for past-`expires_at` and deletes it.
4. `count_owners`, `set_role`, `set_status`, `set_password_hash`, `user_exists`.

**Tests first (Service):**
5. `bootstrap_owner`: first call creates owner+participant+session (returns raw token once); second call → `Already bootstrapped` error (409).
6. `login`: valid → session row with token **hash** stored, raw returned; wrong password → `InvalidCredentials` **and** `last_login_at` untouched; unknown username → same error + same elapsed profile (dummy-verify runs); `password_hash IS NULL` → `InvalidCredentials`; `status=deactivated` → `AccountDisabled`.
7. `deactivate_user`: sets status + deletes sessions; refuses demoting/deactivating the **last owner** (`NotLastOwnerGuard`); refuse self-deactivation of the acting owner.
8. `resolve_session(token)`: valid → `(user, session)`, slides expiry; expired/unknown/disabled user → `None`/error per type.

**Tests first (CLI):** `reset-password <username> [--password-file -]` — sets hash for existing user, exits nonzero with message for unknown username. (Prompt-less: reads from stdin or `--password-file`; never argv.)

**Implementation notes:** `AuthStore(session)` never commits (VaultStore convention — callers own transactions; routes commit via their own `session.commit()`). `AuthService(store, hasher, settings)`. The two spec seams are exactly `provision_user(...)` and `issue_session(user_id)`; every future method routes through them. Opportunistic expired-row cleanup inside `login` only.

**Commit:** `feat(auth): AuthStore, AuthService (bootstrap/login/logout/deactivate), reset-password CLI`

## Phase 3 — HTTP surface (2 commits)

### Task 4: deps + routes

**Files:** `auth/deps.py`, `routes/auth.py`, `app.py` (mount router), `middleware.py` (`add_error_handlers` gains `AuthError → JSONResponse` mapping), `tests/auth/test_deps.py`, `tests/routes/__init__.py`, `tests/routes/test_auth_routes.py`

**Tests first (httpx `ASGITransport` against real `app`, dummy hasher, sqlite temp DB — copy wiring from `tests/conftest.py`):**
1. `POST /api/auth/status` → `{setup_required: true}` empty DB; `false` after bootstrap. Public.
2. `POST /api/auth/bootstrap` (username/password/display_name) → 201 + `Set-Cookie: octave_session=…; HttpOnly; SameSite=lax; Path=/; Max-Age=…` (`Secure` only when `cookie_secure=True`); immediate `GET /api/auth/me` → 200 identity. Second bootstrap → 409 forever.
3. `POST /api/auth/login` → cookie set, `last_login_at` updated. Wrong creds → 401, no cookie.
4. `POST /api/auth/logout` → cookie cleared + row gone; next `me` → 401.
5. `GET /api/auth/users` / `POST /api/auth/users` / `PATCH /api/auth/users/{id}` → 200/201/200 for owner; 403 member; 401 anonymous. Last-owner PATCH demote/deactivate → 409.
6. Expired token → 401 (monkeypatch clock or insert past `expires_at`).
7. **Guard test:** import `app`, walk `app.routes`; every route path outside allowlist `{/api/auth/status, /api/auth/bootstrap, /api/health, /api/version, /ws, docs/openapi}` must have `get_current_user` in its dependency tree (`route.dependant` walk). This is the regression gate.

**Implementation notes:** `get_current_user(request, session=Depends(get_db_session))` reads the `octave_session` cookie → `AuthService.resolve_session`; raises `HTTPException(401)` (www-authenticate omitted). `require_owner` depends on `get_current_user`. Routes commit after service success. Pydantic request models validate username pattern `[a-z0-9._-]{2,32}` server-side. Rate limiter: in-process dict (username, client host) → 5 failures/15 min → 429; advisory, comment it.

**Commit:** `feat(auth): identity deps, auth routes, error mapping, anonymous-rejection guard test`

### Task 5: WebSocket handshake auth

**Files:** `websocket/connection.py`, `tests/test_websocket.py`

**Tests first:**
1. WS connect without cookie → socket closed with code `4401` **before** accept (starlette TestClient records `websocket_close`).
2. Valid cookie → accept + existing join flow unchanged.
3. `join` on another user's session (user authenticated but not participant/owner) → close `4403`.

**Implementation notes:** resolve cookie via the same `AuthService.resolve_session` (build a transient `AsyncSession` from `app.state.db_session_factory` — WS scope has no request-scoped dep). Record the known gap (open socket survives deactivation until reconnect) as a code comment pointing at the spec.

**Commit:** `feat(websocket): authenticate handshake via session cookie (4401/4403)`

## Phase 4 — Frontend (2 commits)

### Task 6: API client + auth state

**Files:** `src/lib/api/client.ts` (`credentials: 'include'` + `patch()` helper + `fetchMe`/`login`/`logout`/`bootstrap`/`fetchAuthStatus` + `AuthUser` type), `src/lib/auth/useAuth.tsx` (context: `user`, `status: 'loading'|'anonymous'|'authed'|'setup_required'`, actions; on mount: status probe → `fetchMe`), `src/lib/api/client.test.ts`, `src/lib/auth/useAuth.test.tsx`

**Tests first:** client sends `credentials: 'include'` (mock fetch, assert init); 401 on a non-auth path triggers `onUnauthorized` hook (used to clear auth state); `useAuth` state transitions: setup_required / authed / login success → authed / logout → anonymous.

**Commit:** `feat(ui): auth-aware API client and useAuth state`

### Task 7: Login/Setup views + route guard + E2E

**Files:** `src/views/LoginView.tsx`, `src/views/SetupView.tsx`, `src/components/auth/RequireAuth.tsx`, `src/router.tsx` (guard app shell; `/login`, `/setup`), `frontend/tests/views/LoginView.test.tsx`, `SetupView.test.tsx`, `RequireAuth.test.tsx`, `tests/e2e/auth.spec.ts`

**Tests first:** `RequireAuth` redirects anonymous → `/login`, setup_required → `/setup`, authed renders children. `SetupView` renders only when `setup_required`; posts bootstrap → authed. `LoginView` shows field error on 401. Playwright: bootstrap → login → logout happy path against the real backend (reuse `shell.spec.ts` wiring pattern; if the E2E harness can't boot the backend, mark the spec `skip` with a TODO and note it — do not fake-pass).

**Commit:** `feat(ui): login/setup views, RequireAuth guard, auth E2E`

## Phase 5 — Docs & closeout (1 commit)

### Task 8: ADR amendment + decision memory + follow-up issues

1. `.agents/memory/decisions.md`: amendment entry — *multi-user **login** is compatible with the 2026-06-27 local-first ADR (accounts per-instance, data on-prem); **sync** remains out of scope.*
2. File follow-up issues via create-work-item skill (Post-1.0 milestone unless noted): OIDC integration incl. `auth_identities` landing shape; WS revalidation on heartbeat; double-submit CSRF token (if a GET ever mutates); shared-state rate limiting (**pre-internet-exposure**, label `high`); optional `require_owner` gating on MCP/inference config.
3. Update #122 body checkboxes; PR = `feature/multi-user-auth` → `develop`.

**Commit:** `docs(auth): ADR amendment — local-first permits multi-user login, not sync`

---

## Success criteria (mirror of spec §Success criteria — all must pass)

1. `pytest -q`, `ruff check .`, `mypy --strict src/` green in `backend/`; `npm run test -- --run` + `npm run lint` green in `frontend/`.
2. Fresh DB: status→bootstrap→cookie→`me` round-trip; second bootstrap 409.
3. Guard test green (no anonymous non-public route).
4. Deactivation fails existing session on next request.
5. Pre-auth DB migration test green (rows survive, oldest→owner, login blocked until reset).
6. WS rejects unauthenticated handshake pre-accept (`4401`).
7. ADR amendment present.

## Ordering & rationale

Tasks are strictly sequential (1→2→3→4→5→6→7→8): each builds on the previous commit's surface. Within a task, test-first per TDD skill. Stop-and-ask triggers: migration conflicts on rebase, alembic autogenerate fighting the hand-written backfill (write it by hand — do not trust autogenerate for the data steps), any need to change the spec (→ Architect, don't improvise).
