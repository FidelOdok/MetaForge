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

  test('History tab shows a parameter diff against a superseded version (FORGE-270)', async ({
    page,
  }) => {
    await page.goto('/twin?demo=1');
    // The agent chat panel is open by default and overlaps the graph canvas.
    await page.getByRole('button', { name: 'Agent' }).click();
    await page.getByText('enclosure.step').click();
    await page.getByRole('button', { name: 'history' }).click();

    const diff = page.getByTestId('feature-version-diff');
    await expect(diff).toBeVisible();
    await expect(diff).toContainText('wall_mm');
    await expect(diff).toContainText('3');
    await expect(diff).toContainText('2');
  });
});

test.describe('Requirements', () => {
  test('renders quality flags, conflicts, and completeness from the sample workspace', async ({
    page,
  }) => {
    await page.goto('/requirements?demo=1');
    // Scoped to the quality table specifically -- the evidence matrix
    // table (FORGE-318) also renders a `mass_limit` row on this same
    // page, so an unscoped `getByRole('table')` is ambiguous.
    const table = page.getByTestId('requirements-quality-table');
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

  test('constraint editor records a structured requirement (FORGE-259)', async ({ page }) => {
    await page.goto('/requirements?demo=1');
    const matrix = page.getByTestId('requirements-matrix');
    await expect(matrix).toBeVisible();

    await matrix.getByTestId('new-constraint-button').click();
    const form = page.getByTestId('new-constraint-form');
    await expect(form).toBeVisible();

    await form.getByLabel('Name').fill('tip_deflection');
    await form.getByLabel('Metric').fill('tip_deflection');
    await form.getByLabel('Limit').fill('0.5');
    await form.getByLabel('Unit').fill('mm');
    await form.getByLabel('Target node type').fill('cad_model');
    await form.getByRole('button', { name: 'Record' }).click();

    // The form closes and the new requirement appears in the matrix,
    // live pass/fail/no_data status computed the same way every other
    // row's is -- honestly no_data since no claim cites it yet.
    await expect(form).not.toBeVisible();
    const newRow = matrix.getByRole('row', { name: /tip_deflection/ });
    await expect(newRow).toContainText('no_data');
    await expect(newRow).toContainText('tip_deflection <= 0.5mm');
  });

  test('verification declaration drives the unverified badge (FORGE-258)', async ({ page }) => {
    await page.goto('/requirements?demo=1');
    const matrix = page.getByTestId('requirements-matrix');
    await expect(matrix).toBeVisible();

    // Undeclared: no verification method, no expected evidence -- red
    // "not declared" badge, distinct from the pass/fail/no_data status pill.
    await matrix.getByTestId('new-constraint-button').click();
    let form = page.getByTestId('new-constraint-form');
    await form.getByLabel('Name').fill('req_no_verification');
    await form.getByLabel('Metric').fill('req_no_verification');
    await form.getByLabel('Limit').fill('1.0');
    await form.getByRole('button', { name: 'Record' }).click();
    await expect(form).not.toBeVisible();

    const unverifiedRow = matrix.getByRole('row', { name: /req_no_verification/ });
    await expect(unverifiedRow.getByTestId('unverified-badge')).toBeVisible();
    await expect(unverifiedRow.getByTestId('unverified-badge')).toContainText('not declared');

    // Declared: verification method + expected evidence both set -- no red
    // badge, and both values render on the row.
    await matrix.getByTestId('new-constraint-button').click();
    form = page.getByTestId('new-constraint-form');
    await form.getByLabel('Name').fill('req_has_verification');
    await form.getByLabel('Metric').fill('req_has_verification');
    await form.getByLabel('Limit').fill('2.0');
    await form.getByLabel('Verification method').fill('FEA');
    await form.getByLabel('Expected evidence').selectOption('simulation');
    await form.getByRole('button', { name: 'Record' }).click();
    await expect(form).not.toBeVisible();

    const verifiedRow = matrix.getByRole('row', { name: /req_has_verification/ });
    await expect(verifiedRow.getByTestId('unverified-badge')).not.toBeVisible();
    await expect(verifiedRow).toContainText('FEA');
    await expect(verifiedRow).toContainText('simulation');
  });

  test('closed design loop runs, shows the iteration timeline, and approves the winner (FORGE-287)', async ({
    page,
  }) => {
    await page.goto('/requirements?demo=1');
    const loopSection = page.getByTestId('design-loop-section');
    await expect(loopSection).toBeVisible();

    await loopSection.getByTestId('start-design-loop-button').click();
    const form = loopSection.getByTestId('design-loop-form');
    await expect(form).toBeVisible();

    await form.getByLabel('CAD work product id').fill('sample-work-product');
    await form.getByLabel('Load (N)').fill('800');
    await form.getByLabel('Deflection limit (mm)').fill('0.5');
    await form.getByRole('button', { name: 'Run' }).click();

    await expect(form).not.toBeVisible();
    const rows = loopSection.getByTestId('design-loop-iteration-row');
    await expect(rows.first()).toBeVisible();
    await expect(rows).toHaveCount(7);

    // Exactly one converged winner, awaiting approval.
    await expect(loopSection.getByText('awaiting approval')).toBeVisible();
    await expect(loopSection.getByTestId('design-loop-approved-badge')).not.toBeVisible();

    await loopSection.getByTestId('approve-design-loop-button').click();
    await expect(loopSection.getByTestId('design-loop-approved-badge')).toBeVisible();
    await expect(loopSection.getByTestId('approve-design-loop-button')).not.toBeVisible();
  });

  test('gate review blocks on unsatisfied claims, then a reviewer can approve or reject with comment (FORGE-290)', async ({
    page,
  }) => {
    await page.goto('/requirements?demo=1');
    const gateSection = page.getByTestId('gate-review-section');
    await expect(gateSection).toBeVisible();

    await gateSection.getByTestId('open-gate-review-button').click();
    const form = gateSection.getByTestId('gate-review-form');
    await expect(form).toBeVisible();

    // mass_limit is a real FAIL row in the sample matrix -- picking it and
    // approving should block on real (illustrative) evidence, not on
    // missing reviewer identity.
    await form.getByRole('checkbox').first().check();
    await form.getByLabel('Reviewer').fill('reviewer@example.com');
    await form.getByTestId('approve-gate-button').click();

    const result = gateSection.getByTestId('gate-review-result');
    await expect(result).toBeVisible();
    await expect(result).toContainText('blocked');

    // Now pick ONLY vague_speed, a real PASS row -- but REJECT it anyway
    // with a comment: the human veto overrides even satisfied evidence.
    await form.getByRole('checkbox').first().uncheck();
    await form.getByRole('checkbox').nth(1).check();
    await form.getByLabel('Comment').fill('hold off, want a second opinion');
    await form.getByTestId('reject-gate-button').click();

    await expect(result).toContainText('blocked');
    await expect(result).toContainText('hold off, want a second opinion');

    // Both attempts appear in the history.
    const history = gateSection.getByTestId('gate-review-history');
    await expect(history).toContainText('reviewer@example.com');
  });

  test('feature library generates a bolt pattern and a rib (FORGE-269)', async ({ page }) => {
    await page.goto('/requirements?demo=1');
    const featureSection = page.getByTestId('feature-library-section');
    await expect(featureSection).toBeVisible();

    await featureSection.getByTestId('open-feature-library-button').click();
    const form = featureSection.getByTestId('feature-library-form');
    await expect(form).toBeVisible();

    // Default feature is bolt_pattern -- fill the name and generate.
    await form.getByLabel('Name').fill('Motor mount bolt pattern');
    await form.getByRole('button', { name: 'Generate' }).click();

    await expect(form).not.toBeVisible();
    let result = featureSection.getByTestId('feature-library-result');
    await expect(result).toBeVisible();
    await expect(result).toContainText('bolt_pattern');
    await expect(result).toContainText('6 entities');

    // Switch to rib and generate again.
    await featureSection.getByTestId('open-feature-library-button').click();
    await form.getByLabel('Feature').selectOption('rib');
    await form.getByLabel('Name').fill('Wall reinforcement rib');
    await form.getByRole('button', { name: 'Generate' }).click();

    await expect(form).not.toBeVisible();
    result = featureSection.getByTestId('feature-library-result');
    await expect(result).toContainText('rib');
    await expect(result).toContainText('3 entities');
  });
});

test.describe('Design Assistant', () => {
  test('renders page content', async ({ page }) => {
    await page.goto('/assistant');
    await expect(page.locator('main')).toBeVisible();
  });
});
