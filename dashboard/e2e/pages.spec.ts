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

  test('flat/hierarchical toggle switches views', async ({ page }) => {
    await page.goto('/bom?demo=1');
    const toggle = page.getByRole('group', { name: 'BOM view' });
    await expect(toggle.getByRole('button', { name: 'flat' })).toHaveAttribute('aria-pressed', 'true');
    await toggle.getByRole('button', { name: 'hierarchical' }).click();
    await expect(toggle.getByRole('button', { name: 'hierarchical' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });
});

test.describe('Digital Twin Viewer', () => {
  test('renders page content', async ({ page }) => {
    await page.goto('/twin');
    await expect(page.locator('main')).toBeVisible();
  });

  test('Structure tab renders the hierarchy tree area', async ({ page }) => {
    await page.goto('/twin?demo=1');
    await page.getByRole('button', { name: 'Structure', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Structure' })).toBeVisible();
  });

  test('Assembly tab: joints are editable and persist', async ({ page }) => {
    await page.goto('/twin?demo=1');
    // The agent chat panel is open by default and overlaps the graph canvas.
    await page.getByRole('button', { name: 'Agent' }).click();
    await page.getByText('drone-assembly.urdf').click();
    await page.getByRole('button', { name: 'Assembly', exact: true }).click();

    // The sample node's existing joint is shown.
    const editor = page.getByTestId('assembly-joints-editor');
    await expect(editor.getByText(/rotor_joint/)).toBeVisible();

    // Add a new joint, picking base/follower from the assembly's own parts.
    await editor.getByRole('button', { name: '+ Add mate/joint' }).click();
    await editor.getByLabel('Name').fill('tail_joint');
    await editor.getByLabel('Base part').selectOption('base');
    await editor.getByLabel('Follower part').selectOption('rotor');
    await editor.getByRole('button', { name: 'Add joint' }).click();

    await expect(editor.getByText(/tail_joint/)).toBeVisible({ timeout: 10_000 });

    // Delete the original joint; it disappears, the new one survives.
    await editor.getByRole('button', { name: 'Delete joint rotor_joint' }).click();
    await expect(editor.getByText(/rotor_joint/)).not.toBeVisible({ timeout: 10_000 });
    await expect(editor.getByText(/tail_joint/)).toBeVisible();
  });
});

test.describe('Design Assistant', () => {
  test('renders page content', async ({ page }) => {
    await page.goto('/assistant');
    await expect(page.locator('main')).toBeVisible();
  });
});
