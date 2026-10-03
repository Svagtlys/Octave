import { test, expect } from '@playwright/test';

test('sidebar navigation switches views', async ({ page }) => {
  await page.goto('/sessions');
  await page.getByRole('link', { name: 'Vault' }).click();
  await expect(page).toHaveURL(/\/vault$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Vault' })).toBeVisible();

  await page.getByRole('link', { name: 'MCP' }).click();
  await expect(page).toHaveURL(/\/mcp$/);
  await expect(page.getByRole('heading', { level: 1, name: 'MCP Servers' })).toBeVisible();
});

test('deep link to /settings renders the Settings view', async ({ page }) => {
  await page.goto('/settings');
  await expect(page.getByRole('heading', { level: 1, name: 'Settings' })).toBeVisible();
});

test('unknown route renders Not Found inside the shell', async ({ page }) => {
  await page.goto('/does-not-exist');
  await expect(page.getByRole('heading', { level: 2, name: 'Not Found' })).toBeVisible();
  // Sidebar brand scoped to the complementary landmark (the TopBar h1 also
  // falls back to "Octave" when the not-found route has no staticData title).
  await expect(page.getByRole('complementary').getByText('Octave')).toBeVisible();
});

test('health pill renders in one of its three states', async ({ page }) => {
  await page.goto('/sessions');
  const pill = page.getByTestId('health-pill');
  await expect(pill).toBeVisible();
  await expect(pill).toContainText(/Healthy|Unreachable|Checking/);
});
