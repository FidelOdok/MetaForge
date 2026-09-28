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

  test('results: selecting two rows shows a version-compare panel', async ({ page }) => {
    await page.goto('/sim?demo=1');

    await expect(page.getByRole('heading', { name: 'Results' })).toBeVisible();
    const rev1Row = page.getByRole('row', { name: /Rev 1 FEA/ });
    const rev2Row = page.getByRole('row', { name: /Rev 2 FEA/ });
    await expect(rev1Row).toBeVisible();
    await expect(rev2Row).toBeVisible();

    // No comparison until exactly two are selected.
    await expect(page.getByText(/→/)).toHaveCount(0);

    await rev1Row.getByRole('checkbox').check();
    await rev2Row.getByRole('checkbox').check();

    const compare = page.getByTestId('results-compare');
    await expect(compare.getByText('Rev 1 FEA — static 1g → Rev 2 FEA — static 1g')).toBeVisible();
    await expect(compare.getByText('Max stress')).toBeVisible();
    await expect(compare.getByText('Max displacement')).toBeVisible();
    // Stress dropped from rev1 to rev2 (62.4 -> 45.1 MPa) — a negative delta.
    await expect(compare.getByText(/-27\.7%/)).toBeVisible();
  });
});
