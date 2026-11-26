# Delete Session Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `DELETE /api/sessions/{id}` — owner-only hard delete with DB-level FK cascade — and unskip the frontend delete e2e (issue #160).

**Architecture:** Two new `SessionStore` methods (`get_session_owner`, `delete_session`; never-commit, HTTP-agnostic like every plane store) behind a new FastAPI route that maps owner verdicts to 204/404/403. Child rows (`session_participants`, `events`, `agent_instances`, child sessions via `parent_session_id`) are removed by schema-level `ondelete="CASCADE"` with `PRAGMA foreign_keys=ON` — no app-side child deletes.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, SQLite (`create_sqlite_engine`), pytest + pytest-asyncio; React/TypeScript frontend (Playwright e2e, no logic changes).

**Spec:** [`.agents/specs/2026-11-26-delete-session-endpoint-design.md`](2026-11-26-delete-session-endpoint-design.md) · **Branch:** `feature/delete-session-endpoint` · **PR:** https://github.com/Svagtlys/Octave/pull/164

**Conventions you need:**
- Run backend commands from `backend/` (`uv run pytest …`). Frontend from `frontend/`.
- Plane stores **never commit**; routes own transaction boundaries (`octave.db.deps.get_db_session` rolls back on exception, commits are the route's job — see `create_session`).
- Route error details are plain strings; `octave.middleware.add_error_handlers` maps `DbError` → 500 globally.
- 401 comes free from `Depends(get_current_user)` (cookie `octave_session`).

---

### Task 1: `SessionStore.get_session_owner`

**Files:**
- Modify: `backend/src/octave/db/session_store.py` (add method to `SessionStore`, after `resolve_access` ~line 212)
- Test: `backend/tests/db/test_session_store.py` (append at end)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/db/test_session_store.py` (the file's existing helpers `_seed_user` / `_make_session` are reused; imports for `select`, `Session`, `SessionStore` already exist at the top):

```python
# -- get_session_owner / delete_session (issue #160) ---------------------------


async def test_get_session_owner_hit_and_miss(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        store = SessionStore(session)
        assert await store.get_session_owner("s_1") == "u_1"
        assert await store.get_session_owner("s_ghost") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/db/test_session_store.py::test_get_session_owner_hit_and_miss -v`
Expected: FAIL — `AttributeError: 'SessionStore' object has no attribute 'get_session_owner'`

- [ ] **Step 3: Write the minimal implementation**

In `backend/src/octave/db/session_store.py`, add inside `class SessionStore` directly after `resolve_access`:

```python
    async def get_session_owner(self, session_id: str) -> str | None:
        """``created_by_user_id`` for the row, or ``None`` when absent (issue
        #160). The DELETE guard's read half: the route compares the owner to
        the caller (owner-only delete; participant reads stay resolve_access).
        Never commits."""
        return await self._session.scalar(
            select(Session.created_by_user_id).where(Session.id == session_id)
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/db/test_session_store.py::test_get_session_owner_hit_and_miss -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/octave/db/session_store.py backend/tests/db/test_session_store.py
git commit -m "feat(db): SessionStore.get_session_owner for DELETE guard (issue #160)"
```

---

### Task 2: `SessionStore.delete_session` + cascade tests

**Files:**
- Modify: `backend/src/octave/db/session_store.py` (add `delete` import; add method after `get_session_owner`)
- Test: `backend/tests/db/test_session_store.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/db/test_session_store.py`. Add `delete` to the sqlalchemy import at the top of the file (line 11 becomes `from sqlalchemy import delete, func, select`) and add `AgentInstance` to the `octave.db.models` import block (lines 15-22).

```python
async def test_delete_session_removes_row_and_returns_rowcount(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        await session.commit()
        assert await SessionStore(session).delete_session("s_1") == 1
        await session.commit()
    async with session_factory() as check:
        assert await check.get(Session, "s_1") is None
        assert await SessionStore(check).delete_session("s_1") == 0


async def test_delete_session_never_commits(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An uncommitted delete is invisible to a second session (store contract,
    mirrors test_create_session_never_commits)."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        await session.commit()
    async with session_factory() as session:
        await SessionStore(session).delete_session("s_1")
        # no commit — the context manager closes without committing
    async with session_factory() as check:
        assert await check.get(Session, "s_1") is not None


async def test_delete_session_cascades_children(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Schema-level ondelete=CASCADE (PRAGMA foreign_keys=ON via
    create_sqlite_engine) removes membership, events, agent instances and the
    child session; unrelated rows and participant identities survive."""
    async with session_factory() as session:
        await _seed_user(
            session, user_id="u_1", participant_id="p_1", display_name="Alice"
        )
        session.add(Agent(id="a_1", name="Helper"))
        session.add(Participant(id="p_agent", agent_id="a_1", label="Helper"))
        await _make_session(
            session, session_id="s_1", owner_user_id="u_1", created_at=_dt(1)
        )
        await _make_session(
            session, session_id="s_other", owner_user_id="u_1", created_at=_dt(1)
        )
        child = Session(
            id="s_child",
            created_by_user_id="u_1",
            status="active",
            parent_session_id="s_1",
        )
        session.add(child)
        session.add(
            SessionParticipant(
                session_id="s_1", participant_id="p_agent", role="speaker"
            )
        )
        session.add(
            AgentInstance(id="ai_1", agent_id="a_1", session_id="s_1")
        )
        await _append(
            session, "s_1", EventKind.USER_MESSAGE, "hi", author="p_1"
        )
        await _append(
            session, "s_child", EventKind.USER_MESSAGE, "child hi", author="p_1"
        )
        await session.commit()

    async with session_factory() as session:
        assert await SessionStore(session).delete_session("s_1") == 1
        await session.commit()

    async with session_factory() as check:
        assert await check.get(Session, "s_1") is None
        assert await check.get(Session, "s_child") is None
        assert await check.get(Session, "s_other") is not None
        for model, session_id in [
            (SessionParticipant, "s_1"),
            (Event, "s_1"),
            (Event, "s_child"),
            (AgentInstance, "s_1"),
        ]:
            count = await check.scalar(
                select(func.count()).select_from(model).where(
                    model.session_id == session_id
                )
            )
            assert count == 0, model
        # Identity rows are NOT session data — they survive.
        assert await check.get(Participant, "p_1") is not None
        assert await check.get(Participant, "p_agent") is not None
        assert await check.get(Agent, "a_1") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/db/test_session_store.py -k delete_session -v`
Expected: FAIL — `AttributeError: 'SessionStore' object has no attribute 'delete_session'`

- [ ] **Step 3: Write the minimal implementation**

In `backend/src/octave/db/session_store.py`, extend the sqlalchemy import (line 21) to `from sqlalchemy import delete, func, select`, then add inside `class SessionStore` after `get_session_owner`:

```python
    async def delete_session(self, session_id: str) -> int:
        """Hard delete of the sessions row; returns rowcount (issue #160).
        DB-level FK CASCADE (PRAGMA foreign_keys=ON) removes
        session_participants, events, agent_instances and child sessions —
        no app-side child deletes. Bulk statement: no ORM object load.
        Never commits."""
        result = await self._session.execute(
            delete(Session).where(Session.id == session_id)
        )
        return int(result.rowcount or 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/db/test_session_store.py -k delete_session -v`
Expected: 3 PASS

- [ ] **Step 5: Run the full store suite (no regressions)**

Run: `cd backend && uv run pytest tests/db/test_session_store.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/octave/db/session_store.py backend/tests/db/test_session_store.py
git commit -m "feat(db): SessionStore.delete_session with cascade coverage (issue #160)"
```

---

### Task 3: `DELETE /api/sessions/{session_id}` route

**Files:**
- Modify: `backend/src/octave/routes/sessions.py` (import `Response`; add route after `list_session_messages`)
- Test: `backend/tests/routes/test_sessions.py` (append; file header imports already include `select`, `text`, `Session`, `SessionParticipant`, `Event` — add `AgentInstance` to the `octave.db.models` import at line 22)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/routes/test_sessions.py`:

```python
# -- DELETE /sessions/{id} (issue #160) -----------------------------------------


async def test_delete_requires_auth(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        response = await client.delete("/api/sessions/whatever")
        assert response.status_code == 401


async def test_delete_unknown_session_404(app_and_factory) -> None:
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        response = await client.delete("/api/sessions/nope")
        assert response.status_code == 404
        assert response.json()["detail"] == "Session not found"


async def test_delete_non_owner_participant_403(app_and_factory) -> None:
    """A live participant may read (issue #151) but must not destroy the
    owner's transcript — delete is owner-only."""
    app, factory = app_and_factory
    async with await _client(app) as owner:
        await _bootstrap(owner)  # alice
        bob_id = await _create_member(owner, "bob")
        session_id = (await owner.post("/api/sessions", json={})).json()["id"]
        bob_pid = await _participant_id(factory, bob_id)
    async with factory() as db:
        db.add(SessionParticipant(session_id=session_id, participant_id=bob_pid))
        await db.commit()
    async with await _client(app) as bob:
        await _login(bob, "bob")
        response = await bob.delete(f"/api/sessions/{session_id}")
        assert response.status_code == 403
    async with factory() as db:
        assert await db.get(Session, session_id) is not None  # untouched


async def test_delete_owner_removes_session_and_children_204(
    app_and_factory,
) -> None:
    """204 empty body; participants/events/agent_instances rows cascade away
    and the session disappears from the caller's list."""
    app, factory = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        me = (await client.get("/api/auth/me")).json()
        session_id = (await client.post("/api/sessions", json={})).json()["id"]
        alice = await _participant_id(factory, me["id"])
    async with factory() as db:
        db.add(Agent(id="a_1", name="Helper"))
        db.add(Participant(id="p_agent", agent_id="a_1", label="Helper"))
        db.add(AgentInstance(id="ai_1", agent_id="a_1", session_id=session_id))
        await db.commit()
    await _seed_activity(
        factory,
        session_id,
        [
            (EventKind.USER_MESSAGE, "hi", _dt(5)),
            (EventKind.TOOL_CALL, "tool", _dt(6)),
        ],
        author=alice,
    )
    async with await _client(app) as client:
        await _bootstrap_login(client)
        response = await client.delete(f"/api/sessions/{session_id}")
        assert response.status_code == 204
        assert response.content == b""
        listing = (await client.get("/api/sessions")).json()
        assert [item["id"] for item in listing["items"]] == []
        assert listing["total"] == 0

    async with factory() as db:
        assert await db.get(Session, session_id) is None
        for model in (SessionParticipant, Event, AgentInstance):
            count = await db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.session_id == session_id)
            )
            assert count == 0, model


async def test_delete_repeat_404(app_and_factory) -> None:
    """Repeat delete answers 404 like never-existed (decision 2)."""
    app, _ = app_and_factory
    async with await _client(app) as client:
        await _bootstrap(client)
        session_id = (await client.post("/api/sessions", json={})).json()["id"]
        assert (await client.delete(f"/api/sessions/{session_id}")).status_code == 204
        response = await client.delete(f"/api/sessions/{session_id}")
        assert response.status_code == 404
        assert response.json()["detail"] == "Session not found"
```

Also update the test-file imports: add `func` to the `sqlalchemy` import (line 14 becomes `from sqlalchemy import func, select, text`) and `Agent, AgentInstance` to the `octave.db.models` import (line 22 becomes `from octave.db.models import (Agent, AgentInstance, Base, Participant, Session, SessionParticipant)`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/routes/test_sessions.py -k delete -v`
Expected: FAIL — `405 Method Not Allowed` (route does not exist; the 405 assertion failures prove the surface is missing)

- [ ] **Step 3: Write the implementation**

In `backend/src/octave/routes/sessions.py`: add `Response` to the fastapi import (line 15 becomes `from fastapi import APIRouter, Depends, HTTPException, Query, Response`), then append after `list_session_messages` / `_to_message_response`:

```python
@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_db_session),
) -> Response:
    """Hard delete an owned session (issue #160). Owner-only: a live
    participant may read but never destroy the transcript. 404 unknown id
    (repeat delete included), 403 exists-but-not-owner, 204 on success with
    an empty body. Children (participants/events/agent_instances/child
    sessions) go via schema-level FK CASCADE; the route owns the commit."""
    store = SessionStore(session)
    owner = await store.get_session_owner(session_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if owner != user.id:
        raise HTTPException(
            status_code=403, detail="Only the owner can delete this session"
        )
    await store.delete_session(session_id)
    await session.commit()
    return Response(status_code=204)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/routes/test_sessions.py -k delete -v`
Expected: 5 PASS

- [ ] **Step 5: Run the full backend suite + lint + types**

Run: `cd backend && uv run pytest tests/ -q && uv run ruff check src/ tests/ && uv run mypy src/`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add backend/src/octave/routes/sessions.py backend/tests/routes/test_sessions.py
git commit -m "feat(api): DELETE /api/sessions/{id} owner-only hard delete (issue #160)"
```

---

### Task 4: Docs + frontend stub-comment removal

**Files:**
- Modify: `docs/API.md` (add section after `GET /api/sessions/{id}/messages`, end of file)
- Modify: `frontend/src/lib/api/sessions.ts:77-83` (comment only — no code change)

- [ ] **Step 1: Add the API.md section**

Append to `docs/API.md`:

```markdown
### `DELETE /api/sessions/{id}`

Hard delete an owned session (issue #160). Owner-only — a current
participant may read but not destroy. Child rows (`session_participants`,
`events`, `agent_instances`, child sessions) are removed by FK cascade.
`204` empty body on success. Errors: `401` unauthenticated, `404` unknown
session (repeat delete included), `403` caller is not the owner.
```

- [ ] **Step 2: Drop the STUB comment in `sessions.ts`**

Replace the doc comment above `deleteSession` (lines 77-82):

```ts
/**
 * Delete a session permanently (issue #160). Owner-only: 404 unknown id,
 * 403 exists-but-not-owner, 204 success (empty body). Child rows go via
 * FK cascade server-side.
 */
export async function deleteSession(sessionId: string): Promise<void> {
```

(The `await deleteRequest<void>(...)` body is unchanged.)

- [ ] **Step 3: Frontend gates**

Run: `cd frontend && npm test && npx tsc --noEmit && npx eslint . && npx prettier --check src`
Expected: all green (comment-only change; `sessions.test.ts` already asserts the real DELETE call)

- [ ] **Step 4: Commit**

```bash
git add docs/API.md frontend/src/lib/api/sessions.ts
git commit -m "docs: DELETE /api/sessions/{id} contract; drop frontend stub comment (issue #160)"
```

---

### Task 5: Unskip + implement the delete e2e

**Files:**
- Modify: `frontend/tests/e2e/chat.spec.ts:227-230` (replace the skipped stub inside `describe('conversation history (real backend)')`)

- [ ] **Step 1: Replace the skipped stub with the real test**

Replace lines 227-230 (`// Delete e2e is blocked…` + `test.skip(...)`) with:

```ts
  test('deleting a conversation removes it from the panel', async ({
    page,
  }) => {
    // Backend DELETE shipped in issue #160 — this was the deferred e2e.
    await page.goto('/');
    const signInButton = page.getByRole('button', { name: /^sign in$/i });
    try {
      await signInButton.waitFor({ state: 'visible', timeout: 10_000 });
      await page.getByLabel('Username').fill(owner.username);
      await page.getByLabel('Password').fill(owner.password);
      await signInButton.click();
    } catch {
      test.info().annotations.push({
        type: 'note',
        description: 'Sign-in form never appeared — owner test did not bootstrap.',
      });
      test.skip();
      return;
    }
    await expect(page).toHaveURL(/\/sessions$/, { timeout: 10_000 });

    // Chat 1 — the survivor. Chat 2 — active, gets deleted.
    await page.getByRole('button', { name: /new chat/i }).click();
    await expect(page).toHaveURL(/\/sessions\/[a-z0-9]+$/);
    const keepUrl = page.url();
    await page.getByLabel('Message input').fill('keep-me marker');
    await page.getByRole('button', { name: /^send$/i }).click();
    await expect(page.getByText('keep-me marker')).toBeVisible({ timeout: 10_000 });

    await page.getByRole('button', { name: /new chat/i }).click();
    await expect.poll(() => page.url(), { timeout: 10_000 }).not.toBe(keepUrl);
    const doomedUrl = page.url();
    await page.getByLabel('Message input').fill('delete-me marker');
    await page.getByRole('button', { name: /^send$/i }).click();
    await expect(page.getByText('delete-me marker')).toBeVisible({ timeout: 10_000 });

    // Delete the ACTIVE conversation via its panel row + confirm modal.
    // Row labels are message first-lines, so the trash aria-label is
    // `Delete conversation delete-me marker` (ConversationList.tsx).
    await page
      .getByRole('button', { name: 'Delete conversation delete-me marker' })
      .click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.getByRole('button', { name: 'Delete', exact: true }).click();

    // Row gone; navigation lands on the next-most-recent (chat 1).
    await expect(
      page.locator('[class*="panel"] li button').filter({ hasText: 'delete-me marker' })
    ).toHaveCount(0);
    await expect(page).toHaveURL(keepUrl, { timeout: 10_000 });
    expect(page.url()).not.toBe(doomedUrl);
    await expect(
      page.getByLabel('Chat messages').getByText('keep-me marker')
    ).toBeVisible({ timeout: 10_000 });
  });
```

- [ ] **Step 2: Type/lint the spec file**

Run: `cd frontend && npx tsc --noEmit && npx eslint tests/e2e/chat.spec.ts && npx prettier --check tests/e2e/chat.spec.ts`
Expected: all green

- [ ] **Step 3: Run the e2e against a real backend (manual probe)**

Backend (fresh DB, separate terminal): `cd backend && OCTAVE_DB_URL="sqlite+aiosqlite:///$(mktemp -d)/e2e.db" uv run uvicorn octave.app:app --port 8000`
Frontend: `cd frontend && VITE_PROXY_TARGET=http://localhost:8000 npm run test:e2e`
Expected: `chat.spec.ts` all green — the delete test runs (no skip) and passes. If no backend is up the probe-skip fires (never fake a pass).

- [ ] **Step 4: Commit**

```bash
git add frontend/tests/e2e/chat.spec.ts
git commit -m "test(e2e): unskip conversation delete flow — DELETE endpoint shipped (issue #160)"
```

---

### Task 6: PR housekeeping

- [ ] **Step 1: Push and flip the PR ready**

```bash
git push origin feature/delete-session-endpoint
gh pr edit 164 --repo Svagtlys/Octave --title "feat: DELETE /api/sessions/{id} owner-only hard delete (issue #160)" --body-file - <<'EOF'
Closes #160

Owner-only `DELETE /api/sessions/{id}`: 204 on success, 404 unknown (incl. repeat), 403 exists-but-not-owner. Children (`session_participants`, `events`, `agent_instances`, child sessions) removed by schema FK cascade. Frontend delete flow (issue #46) now works end-to-end; delete e2e unskipped.

Design: .agents/specs/2026-11-26-delete-session-endpoint-design.md
Plan: .agents/specs/2026-11-26-delete-session-endpoint.md
EOF
gh pr ready 164
```

(Design + plan docs are also uncommitted from the architect session — include them in the docs commit or a dedicated `docs(specs)` commit before pushing.)
