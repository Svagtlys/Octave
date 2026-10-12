import { test, expect, type APIRequestContext } from '@playwright/test';

/**
 * Auth happy path against the REAL backend (issue #122).
 *
 * The Playwright harness (playwright.config.ts) only boots the Vite dev
 * server; the FastAPI backend is not started by it. This spec therefore
 * probes the backend first and skips (rather than fakes) when it is
 * unreachable. To run it locally:
 *
 *   OCTAVE_DB_URL="sqlite+aiosqlite:///$(mktemp -d)/e2e.db" \
 *     uv run uvicorn octave.app:app --port 8000            # in backend/
 *   VITE_PROXY_TARGET=http://localhost:8000 npm run test:e2e  # in frontend/
 *
 * TODO: boot the backend from playwright.config.ts webServer (parallel
 * entries) once an E2E DB story exists, then drop the probe-skip.
 */
const BACKEND = 'http://localhost:8000';

async function backendUp(request: APIRequestContext): Promise<boolean> {
  try {
    const res = await request.get(`${BACKEND}/api/health`);
    return res.ok();
  } catch {
    return false;
  }
}

test.describe('auth happy path (real backend)', () => {
  test.beforeEach(async ({ request }) => {
    test.skip(
      !(await backendUp(request)),
      `no backend at ${BACKEND} — see header of this spec for how to run it`
    );
  });

  test('anonymous deep link bounces to /login or /setup', async ({ page }) => {
    await page.goto('/sessions');
    // Fresh DB -> /setup; already-bootstrapped backend -> /login. Either is
    // a correct bounce; the shell must not render.
    await page.waitForURL(/\/(login|setup)$/);
    expect(page.url()).not.toMatch(/\/sessions$/);
  });

  test('bootstrap -> authed shell -> logout -> login round-trip', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForURL(/\/(login|setup)$/);

    const owner = {
      username: `e2e-${Date.now()}`,
      password: 'e2e-password-123',
      display: 'E2E Owner',
    };

    if (/\/setup$/.test(page.url())) {
      // Fresh instance: create the owner through the setup form.
      await page.getByLabel('Username').fill(owner.username);
      await page.getByLabel('Display name').fill(owner.display);
      await page.getByLabel('Password').fill(owner.password);
      await page.getByRole('button', { name: /create owner/i }).click();
    } else {
      test.info().annotations.push({
        type: 'note',
        description:
          'Backend already bootstrapped — bootstrap leg cannot re-run on a ' +
          'shared DB; login leg below uses a wrong-password check instead.',
      });
      // Already-bootstrapped backend: prove the login form rejects bad creds.
      await page.getByLabel('Username').fill(owner.username);
      await page.getByLabel('Password').fill('definitely-wrong');
      await page.getByRole('button', { name: /^sign in$/i }).click();
      await expect(page.getByRole('alert')).toContainText(
        /Invalid username or password/i
      );
      return;
    }

    // Bootstrap signs straight into the shell.
    await expect(page).toHaveURL(/\/sessions$/);
    await expect(page.getByTestId('current-user')).toBeVisible();

    // Logout returns to the login screen.
    await page.getByRole('button', { name: /sign out/i }).click();
    await expect(page).toHaveURL(/\/login$/);

    // Login with the account just created lands back in the shell.
    await page.getByLabel('Username').fill(owner.username);
    await page.getByLabel('Password').fill(owner.password);
    await page.getByRole('button', { name: /^sign in$/i }).click();
    await expect(page).toHaveURL(/\/sessions$/);
    await expect(page.getByTestId('current-user')).toContainText(owner.display);
  });
});
