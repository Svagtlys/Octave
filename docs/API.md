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
cursor-paginated by `seq` (issue #151; backward paging issue #43). Items
are always `{ seq, kind, author, content, timestamp }`.

Three mutually-exclusive paging modes (`422` if combined):

- **default / `cursor`** (>=0): forward, oldest-first. Omit `cursor` for the
  oldest page; echo `next_cursor` (the last `seq` returned) to advance.
  `next_cursor: null` terminates.
- **`end=true`**: the *newest* page (chat-open), rendered oldest-first within
  the page. `prev_cursor` = oldest `seq` returned, or `null` when no older
  messages remain.
- **`before_seq`** (>=1): the page just below `seq` N, same shape as `end`.

`limit` 1–200, default 50. `200` →
`{ items, next_cursor, prev_cursor }` (`prev_cursor` only set in backward
mode). Errors: `401` unauthenticated, `404` unknown session, `403` caller is
neither owner nor current participant, `422` invalid params.

### `DELETE /api/sessions/{id}`

Hard delete an owned session (issue #160). Owner-only — a current
participant may read but not destroy. Child rows (`session_participants`,
`events`, `agent_instances`, child sessions) are removed by FK cascade.
`204` empty body on success. Errors: `401` unauthenticated, `404` unknown
session (repeat delete included), `403` caller is not the owner.
