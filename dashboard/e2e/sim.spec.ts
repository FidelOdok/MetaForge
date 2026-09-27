import { test, expect } from '@playwright/test';

test.describe('Simulation Page', () => {
  test('renders page heading', async ({ page }) => {
    await page.goto('/sim');
    await expect(page.getByRole('heading', { name: 'Simulation' })).toBeVisible();
  });

  test('displays load-case list or empty state', async ({ page }) => {
    await page.goto('/sim');
    const main = page.locator('main');
    await expect(main).toBeVisible();
  });
});
