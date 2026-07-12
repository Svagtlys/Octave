import { test, expect } from "@playwright/test";

test("app loads and shows Octave heading", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /octave/i })).toBeVisible();
});
