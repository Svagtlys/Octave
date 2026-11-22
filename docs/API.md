# Octave API

Base URL: `/api`. Auth: HttpOnly `octave_session` cookie (see
`docs/ARCHITECTURE.md`).

## Sessions

### `POST /api/sessions`

Create a session owned by the caller. Body: `{ "title"?: string <=200 }`.
`201` → `{ id, title, created_at, last_activity, last_message }`.

### `GET /api/sessions`

The caller's sessions, most-recently-active first. Query: `limit` (1–100,
default 20), `offset` (>=0). `200` → `{ items: Session[], total, limit, offset }`.

### `GET /api/sessions/{id}/messages`

The session transcript (`user_message`/`assistant_message` events only),
oldest-first, cursor-paginated (issue #151). Query: `cursor` (>=0, omit for
the first page — the `seq` to continue after), `limit` (1–200, default 50).
`200` → `{ items: [{ seq, kind, author, content, timestamp }], next_cursor }`
(`next_cursor: null` terminates; echo it back to fetch the next page).
Errors: `401` unauthenticated, `404` unknown session, `403` caller is
neither owner nor current participant, `422` invalid params.
