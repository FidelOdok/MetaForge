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

  test('Structure tab: interfaces and allocation owners are visible (FORGE-313)', async ({
    page,
  }) => {
    await page.goto('/twin?demo=1');
    await page.getByRole('button', { name: 'Structure', exact: true }).click();

    // The ticket's own acceptance example: an interface quantity between
    // upper_arm and shoulder, and a mass allocation with an owner.
    const upperArmRow = page.locator('.tw-structure-row', { hasText: 'upper_arm' });
    await expect(upperArmRow).toBeVisible();
    await expect(page.getByText('shoulder')).toBeVisible();

    const interfaceBadge = upperArmRow.getByLabel('1 interface');
    await expect(interfaceBadge).toBeVisible();
    await expect(interfaceBadge).toHaveAttribute('title', /tip_deflection/);

    const massCell = page.getByTitle('Owner: alice (mechanical)');
    await expect(massCell).toBeVisible();
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

test.describe('Requirements', () => {
  test('renders quality flags, conflicts, and completeness from the sample workspace', async ({
    page,
  }) => {
    await page.goto('/requirements?demo=1');
    const panel = page.getByTestId('requirements-panel');
    const table = panel.getByRole('table');
    await expect(table.getByText('mass_limit')).toBeVisible();
    await expect(table.getByText('vague_speed')).toBeVisible();

    // Completeness: mechanical is covered, safety is flagged missing.
    const completeness = page.getByTestId('requirements-completeness');
    await expect(completeness).toContainText('mechanical');
    await expect(completeness).toContainText('missing: safety');

    // Conflicts: the two mass-bound requirements contradict each other.
    const conflicts = page.getByTestId('requirements-conflicts');
    await expect(conflicts).toContainText('mass_limit');
    await expect(conflicts).toContainText('mass_floor');

    // "Fix with AI" appears on any row with an issue (failing flags or a
    // conflict) -- click it for the vague_speed row and see a proposal.
    const speedRow = table.getByRole('row', { name: /vague_speed/ });
    await speedRow.getByRole('button', { name: 'Fix with AI' }).click();
    await expect(page.getByText(/cruise speed of at least 8 m\/s/)).toBeVisible({
      timeout: 10_000,
    });
  });

  test('evidence matrix shows fail/pass/stale status with click-through evidence (FORGE-318)', async ({
    page,
  }) => {
    await page.goto('/requirements?demo=1');
    const matrix = page.getByTestId('requirements-matrix');
    await expect(matrix).toBeVisible();

    // moving-mass FAIL with real numbers.
    const massRow = matrix.getByRole('row', { name: /mass_limit/ });
    await expect(massRow).toContainText('fail');
    await expect(massRow).toContainText('exceeds limit');

    // deflection-equivalent PASS with tier-0 lineage, revealed on click-through.
    const speedRow = matrix.getByRole('row', { name: /vague_speed/ });
    await expect(speedRow).toContainText('pass');
    await speedRow.getByRole('button', { name: /1 evidence/ }).click();
    await expect(matrix.getByText('twin.evaluate_metric')).toBeVisible();
    await expect(matrix.getByText('tier 0')).toBeVisible();

    // Stale evidence flagged even though the underlying claim is supported.
    const floorRow = matrix.getByRole('row', { name: /mass_floor/ });
    await expect(floorRow).toContainText('stale');

    // Structure-tab click-through.
    await expect(matrix.getByRole('link', { name: 'Structure' }).first()).toHaveAttribute(
      'href',
      '/twin',
    );

    // CSV/MD export buttons are present and produce a download.
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'Export CSV' }).click(),
    ]);
    expect(download.suggestedFilename()).toBe('requirement-matrix.csv');
  });
});

test.describe('Design Assistant', () => {
  test('renders page content', async ({ page }) => {
    await page.goto('/assistant');
    await expect(page.locator('main')).toBeVisible();
  });
});
