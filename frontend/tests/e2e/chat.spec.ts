import { test, expect } from '@playwright/test';

/**
 * Chat transcript view (issue #43) against the REAL backend, using the
 * same probe-skip idiom as auth.spec.ts (no backend -> skip, never fake).
 *
 *   OCTAVE_DB_URL="sqlite+aiosqlite:///$(mktemp -d)/e2e.db" \
 *     uv run uvicorn octave.app:app --port 8000            # in backend/
 *   VITE_PROXY_TARGET=http://localhost:8000 npm run test:e2e  # in frontend/
 *
 * Transcript seeding has no HTTP write path yet (Integration #1 owns
 * POST /sessions/{id}/messages), so this spec pins the reachable flows:
 * an authed empty session renders its empty state, and an unknown session
 * id renders the not-found error — both inside the shell. Row-level
 * rendering (badges, timestamps, ordering) is pinned by unit tests.
 *
 * Serial mode: the file owns exactly one bootstrap on a fresh DB (test 1),
 * then logs in with the same credentials (test 2). On an already-bootstrapped
 * backend both legs skip rather than guess credentials.
 */
const BACKEND = 'http://localhost:8000';

// Deterministic username: the serial pair bootstraps once (test 1) and logs
// back in with the same account (test 2).
const owner = {
  username: 'e2e-chat-owner',
  password: 'e2e-password-123',
  display: 'E2E Chatter',
};

test.describe.configure({ mode: 'serial' });

test.describe('chat transcript view (real backend)', () => {
  test.beforeEach(async ({ request }) => {
    let up = false;
    try {
      up = (await request.get(`${BACKEND}/api/health`)).ok();
    } catch {
      up = false;
    }
    test.skip(!up, `no backend at ${BACKEND} — see header of this spec`);
  });

  test('authed empty session renders the transcript empty state in the shell', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForURL(/\/(login|setup)$/);
    if (/\/login$/.test(new URL(page.url()).pathname)) {
      test.info().annotations.push({
        type: 'note',
        description: 'DB already bootstrapped — empty-session leg needs this spec\'s own owner.',
      });
      test.skip();
      return;
    }

    // Fresh instance: bootstrap this spec's owner, landing in the shell.
    await page.getByLabel('Username').fill(owner.username);
    await page.getByLabel('Display name').fill(owner.display);
    await page.getByLabel('Password').fill(owner.password);
    await page.getByRole('button', { name: /create owner/i }).click();
    await expect(page).toHaveURL(/\/sessions$/);

    // Create a session through the API with the browser's session cookie.
    const created = await page.request.post('/api/sessions', { data: {} });
    expect(created.ok()).toBeTruthy();
    const sessionId = (await created.json()).id as string;

    await page.goto(`/sessions/${sessionId}`);
    await expect(page.getByText(/No messages yet/)).toBeVisible();
    // Still inside the shell — sidebar nav remains visible.
    await expect(page.getByRole('link', { name: /sessions/i })).toBeVisible();
    // No "Load older" affordance for an empty transcript.
    await expect(
      page.getByRole('button', { name: /load older messages/i })
    ).toHaveCount(0);
  });

  test('unknown session id renders the not-found error inside the shell', async ({
    page,
  }) => {
    // Sign in with the owner bootstrapped by the first test (serial mode).
    // Fresh cookie context -> the auth probe bounces to /login; wait for a
    // settled UI state (login form or authed shell), never a transient URL.
    await page.goto('/');
    const signInButton = page.getByRole('button', { name: /^sign in$/i });
    try {
      await signInButton.waitFor({ state: 'visible', timeout: 10_000 });
    } catch {
      test.info().annotations.push({
        type: 'note',
        description: 'Sign-in form never appeared — test 1 did not bootstrap.',
      });
      test.skip();
      return;
    }
    await page.getByLabel('Username').fill(owner.username);
    await page.getByLabel('Password').fill(owner.password);
    await signInButton.click();
    await expect(page).toHaveURL(/\/sessions$/, { timeout: 10_000 });
    // Sign-out button presence proves the authed shell rendered; the
    // user-chip content is pinned by auth.spec.ts, not re-asserted here.
    await expect(page.getByRole('button', { name: /sign out/i })).toBeVisible();

    await page.goto('/sessions/does-not-exist');
    await expect(page.getByRole('alert')).toContainText(/doesn't exist/i);
    await expect(page.getByRole('button', { name: /retry/i })).toBeVisible();
    // Still inside the shell.
    await expect(page.getByRole('link', { name: /sessions/i })).toBeVisible();
  });

  test('sending a message echoes it into the transcript over the websocket', async ({
    page,
  }) => {
    // Sign in with the owner bootstrapped by test 1 (same idiom as the
    // not-found test). The user_message echo is broadcast even when the
    // inference engine is down — the router commits the user_message before
    // the turn runs (#60 error contract), so this leg needs no live Ollama.
    await page.goto('/');
    const signInButton = page.getByRole('button', { name: /^sign in$/i });
    try {
      await signInButton.waitFor({ state: 'visible', timeout: 10_000 });
    } catch {
      test.info().annotations.push({
        type: 'note',
        description: 'Sign-in form never appeared — test 1 did not bootstrap.',
      });
      test.skip();
      return;
    }
    await page.getByLabel('Username').fill(owner.username);
    await page.getByLabel('Password').fill(owner.password);
    await signInButton.click();
    await expect(page).toHaveURL(/\/sessions$/, { timeout: 10_000 });

    const created = await page.request.post('/api/sessions', { data: {} });
    expect(created.ok()).toBeTruthy();
    const sessionId = (await created.json()).id as string;

    await page.goto(`/sessions/${sessionId}`);
    await expect(page.getByText(/No messages yet/)).toBeVisible();

    const unique = `e2e echo ${Date.now()}`;
    await page.getByLabel('Message input').fill(unique);
    await page.getByRole('button', { name: /^send$/i }).click();

    // The composer clears immediately; the message reappears as a transcript
    // row when the hub echo lands.
    await expect(page.getByText(unique)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByLabel('Message input')).toHaveValue('');
  });
});

test.describe('conversation history (real backend)', () => {
  test.beforeEach(async ({ request }) => {
    let up = false;
    try {
      up = (await request.get(`${BACKEND}/api/health`)).ok();
    } catch {
      up = false;
    }
    test.skip(!up, `no backend at ${BACKEND} — see header of this spec`);
  });

  test('new chat creates sessions, the panel lists and switches between them', async ({
    page,
  }) => {
    // Reuse the serially-bootstrapped owner from the describe above; sign in
    // the same way as the not-found test (login form -> shell).
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

    // Hub: start conversation one via the panel button.
    await page.getByRole('button', { name: /new chat/i }).click();
    await expect(page).toHaveURL(/\/sessions\/[a-z0-9]+$/);
    const firstUrl = page.url();

    // Send over the WS so the first conversation has a distinguishable row.
    await page.getByLabel('Message input').fill('first conversation marker');
    await page.getByRole('button', { name: /^send$/i }).click();
    await expect(page.getByText('first conversation marker')).toBeVisible({ timeout: 10_000 });

    // The panel snapshot predates the commit; reload so its preview reflects
    // the backend's last_message before asserting on panel rows.
    await page.reload();
    await expect(page.getByText('first conversation marker')).toBeVisible({ timeout: 10_000 });

    // Second conversation via the panel.
    await page.getByRole('button', { name: /new chat/i }).click();
    await expect(page).toHaveURL(/\/sessions\/[a-z0-9]+$/);
    expect(page.url()).not.toBe(firstUrl);
    await expect(page.getByText(/No messages yet/)).toBeVisible();

    // Panel lists both; switching back restores the marker (transcript isolation).
    const items = page.locator('[class*="panel"] li button');
    await expect(items.filter({ hasText: /New conversation/ }).first()).toBeVisible();
    await items.filter({ hasText: /first conversation marker/ }).first().click();
    await expect(page).toHaveURL(firstUrl);
    await expect(page.getByText('first conversation marker')).toBeVisible();
  });

  // Delete e2e is blocked on the backend DELETE endpoint (separate issue).
  test.skip('deleting a conversation removes it from the panel', () => {
    // Unskip + implement when DELETE /api/sessions/{id} ships.
  });
});
