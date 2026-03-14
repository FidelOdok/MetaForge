import { test, expect } from '@playwright/test';

// The main navigation sidebar (not ChatSidebar)
const navSidebar = 'aside:not([aria-label="Chat sidebar"])';

test.describe('Navigation', () => {
  test('root redirects to /projects', async ({ page }) => {
    await page.goto('/', { waitUntil: 'networkidle' });
    await expect(page).toHaveURL(/\/projects/);
  });

  test('sidebar renders all nav items', async ({ page }) => {
    await page.goto('/projects', { waitUntil: 'networkidle' });
    const sidebar = page.locator(navSidebar);
    await expect(sidebar.getByText('MetaForge', { exact: true })).toBeVisible();
    await expect(sidebar.getByText('Projects')).toBeVisible();
    await expect(sidebar.getByText('Sessions')).toBeVisible();
    await expect(sidebar.getByText('Approvals')).toBeVisible();
    await expect(sidebar.getByText('BOM')).toBeVisible();
    await expect(sidebar.getByText('Digital Twin')).toBeVisible();
    await expect(sidebar.getByText('Design Assistant')).toBeVisible();
  });

  test('navigate to Sessions page', async ({ page }) => {
    await page.goto('/projects');
    await page.locator(navSidebar).getByText('Sessions').click();
    await expect(page).toHaveURL(/\/sessions/);
  });

  test('navigate to Approvals page', async ({ page }) => {
    await page.goto('/projects');
    await page.locator(navSidebar).getByText('Approvals').click();
    await expect(page).toHaveURL(/\/approvals/);
  });

  test('navigate to BOM page', async ({ page }) => {
    await page.goto('/projects');
    await page.locator(navSidebar).getByText('BOM').click();
    await expect(page).toHaveURL(/\/bom/);
  });

  test('navigate to Digital Twin page', async ({ page }) => {
    await page.goto('/projects');
    await page.locator(navSidebar).getByText('Digital Twin').click();
    await expect(page).toHaveURL(/\/twin/);
  });

  test('navigate to Design Assistant page', async ({ page }) => {
    await page.goto('/projects');
    await page.locator(navSidebar).getByText('Design Assistant').click();
    await expect(page).toHaveURL(/\/assistant/);
  });

  test('active nav item is highlighted', async ({ page }) => {
    await page.goto('/sessions');
    const activeLink = page.locator(`${navSidebar} a[href="/sessions"]`);
    await expect(activeLink).toHaveClass(/bg-blue/);
  });
});
