import { test, expect } from '@playwright/test';

test('app loads and redirects to /sessions', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByText('Octave')).toBeVisible();
  await expect(page).toHaveURL(/\/sessions$/);
  await expect(page.getByRole('heading', { level: 1, name: 'Sessions' })).toBeVisible();
});
