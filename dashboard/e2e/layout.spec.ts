import { test, expect } from '@playwright/test';

test.describe('App Layout', () => {
  test('renders sidebar and main content', async ({ page }) => {
    await page.goto('/projects', { waitUntil: 'networkidle' });
    // Use first aside (navigation sidebar), not the ChatSidebar
    await expect(page.locator('aside').first()).toBeVisible();
    await expect(page.locator('main')).toBeVisible();
  });

  test('displays version badge', async ({ page }) => {
    await page.goto('/projects', { waitUntil: 'networkidle' });
    await expect(page.getByText('v0.1', { exact: true })).toBeVisible();
  });

  test('displays platform version in sidebar footer', async ({ page }) => {
    await page.goto('/projects', { waitUntil: 'networkidle' });
    await expect(page.getByText('MetaForge Platform v0.1.0')).toBeVisible();
  });

  test('unknown route does not crash the app', async ({ page }) => {
    const response = await page.goto('/nonexistent-route', { waitUntil: 'networkidle' });
    // SPA should still serve the page (200 from vite)
    expect(response?.ok()).toBeTruthy();
  });
});

test.describe('Responsive Layout', () => {
  test('sidebar is visible on desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto('/projects', { waitUntil: 'networkidle' });
    await expect(page.locator('aside').first()).toBeVisible();
  });
});
