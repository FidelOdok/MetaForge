import { test, expect } from '@playwright/test';

test.describe('Projects Page', () => {
  test('renders page heading', async ({ page }) => {
    await page.goto('/projects');
    await expect(page.getByRole('heading', { level: 2 })).toBeVisible();
  });

  test('displays project list or empty state', async ({ page }) => {
    await page.goto('/projects');
    const main = page.locator('main');
    await expect(main).toBeVisible();
  });
});

test.describe('Sessions Page', () => {
  test('renders page heading', async ({ page }) => {
    await page.goto('/sessions');
    await expect(page.getByRole('heading', { level: 2 })).toBeVisible();
  });
});

test.describe('Approvals Page', () => {
  test('renders page heading', async ({ page }) => {
    await page.goto('/approvals');
    await expect(page.getByRole('heading', { level: 2 })).toBeVisible();
  });
});

test.describe('BOM Page', () => {
  test('renders page heading', async ({ page }) => {
    await page.goto('/bom');
    await expect(page.getByRole('heading', { name: 'Bill of Materials' })).toBeVisible();
  });
});

test.describe('Digital Twin Viewer', () => {
  test('renders page content', async ({ page }) => {
    await page.goto('/twin');
    await expect(page.locator('main')).toBeVisible();
  });
});

test.describe('Design Assistant', () => {
  test('renders page content', async ({ page }) => {
    await page.goto('/assistant');
    await expect(page.locator('main')).toBeVisible();
  });
});
