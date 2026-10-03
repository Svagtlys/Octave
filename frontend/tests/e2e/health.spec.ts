import { test, expect } from '@playwright/test';

test('app loads and redirects to /sessions', async ({ page }) => {
  await page.goto('/');
  // Scoped to the sidebar brand: the TopBar h1 falls back to "Octave" when a
  // route lacks staticData.title, which would trigger a strict-mode violation.
  await expect(page.getByRole('complementary').getByText('Octave')).toBeVisible();
  await expect(page).toHaveURL(/\/sessions$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Sessions' })).toBeVisible();
});
