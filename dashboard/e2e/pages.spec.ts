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

  test('requirement-driven component selection records a margin-checked decision (FORGE-265)', async ({
    page,
  }) => {
    await page.goto('/bom?demo=1');
    const section = page.getByTestId('component-selection-section');
    await expect(section).toBeVisible();

    // One required spec is pre-filled -- name it and set the threshold.
    await section.getByPlaceholder('e.g. torque_kg_cm').fill('torque_kg_cm');
    await section.getByPlaceholder('required value').fill('20');

    // Two candidates are pre-filled by default -- one that clears the
    // requirement, one that doesn't.
    const rows = section.locator('tbody tr');
    await rows.nth(0).getByPlaceholder('e.g. MG996R').fill('DS3218');
    await rows.nth(0).getByPlaceholder('e.g. TowerPro').fill('Miuzei');
    await rows.nth(0).locator('input[type="number"]').fill('19.6');
    await rows.nth(1).getByPlaceholder('e.g. MG996R').fill('MG996R');
    await rows.nth(1).getByPlaceholder('e.g. TowerPro').fill('TowerPro');
    await rows.nth(1).locator('input[type="number"]').fill('25');

    await rows.nth(1).getByTestId('select-candidate-radio-1').check();

    await section.getByPlaceholder('e.g. Elbow joint actuator').fill('Elbow joint actuator');
    await section
      .getByPlaceholder('Why this part, over the others?')
      .fill('MG996R clears the required torque with margin.');

    const selectButton = section.getByTestId('select-component-button');
    await expect(selectButton).toBeEnabled();
    await selectButton.click();

    const result = section.getByTestId('component-selection-result');
    await expect(result).toBeVisible();
    await expect(result).toContainText('MG996R');
    await expect(result).toContainText('meets all required specs');
  });
});

test.describe('Evals Page', () => {
  test('renders per-scenario pass rates and recent nightly runs (FORGE-292)', async ({ page }) => {
    await page.goto('/evals?demo=1');
    await expect(page.getByRole('heading', { name: 'Evals' })).toBeVisible();

    const table = page.getByTestId('evals-table');
    await expect(table).toBeVisible();
    const rows = table.getByTestId('eval-scenario-row');
    await expect(rows).toHaveCount(3);

    // The new outcome-graded design-loop scenario renders alongside the
    // pre-existing keyword-graded ones.
    await expect(table).toContainText('design_loop_wall_thickness');
    await expect(table).toContainText('design_loop_v1');
    await expect(table).toContainText('100%');

    await expect(page.getByTestId('eval-history-badge')).toHaveCount(3);
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

  test('Structure tab: selecting a hierarchy node shows its linked decisions (FORGE-289)', async ({
    page,
  }) => {
    await page.goto('/twin?demo=1');
    // The agent chat panel is open by default and overlaps the tree.
    await page.getByRole('button', { name: 'Agent' }).click();
    await page.getByRole('button', { name: 'Structure', exact: true }).click();

    const upperArmRow = page.locator('.tw-structure-row', { hasText: 'upper_arm' });
    await upperArmRow.getByRole('button', { name: 'upper_arm', exact: true }).click();

    const panel = page.getByTestId('structure-decisions-panel');
    await expect(panel).toBeVisible();
    const card = panel.getByTestId('decision-card');
    await expect(card).toBeVisible();
    await expect(card).toContainText('Upper arm wall thickness');
    await expect(card).toContainText('2 alternatives considered');
    await expect(card).toContainText('Supported by 1 evidence record');
  });

  test('Structure tab: replace placeholder with part (FORGE-266)', async ({ page }) => {
    await page.goto('/twin?demo=1');
    // The agent chat panel is open by default and overlaps the tree.
    await page.getByRole('button', { name: 'Agent' }).click();
    await page.getByRole('button', { name: 'Structure', exact: true }).click();

    const actuatorRow = page.locator('.tw-structure-row', { hasText: 'elbow_actuator' });
    await expect(actuatorRow).toBeVisible();
    await expect(actuatorRow.getByTestId(/^realize-node-button-/)).toHaveText('Replace placeholder');

    await actuatorRow.getByTestId(/^realize-node-button-/).click();
    const panel = page.getByTestId('realize-node-panel');
    await expect(panel).toBeVisible();
    await expect(panel).toContainText('Replace placeholder: elbow_actuator');

    await panel.getByTestId('realize-mode-pick').click();
    await panel.getByTestId('realize-bom-item-select').selectOption({ label: 'DS3218MG -- Miuzei' });
    await panel.getByTestId('confirm-realize-pick').click();

    // The panel closes and the row's own action label flips to "Replace
    // part" -- a real INSTANCE_OF edge now exists, this isn't a placeholder
    // anymore.
    await expect(panel).not.toBeVisible();
    await expect(actuatorRow.getByTestId(/^realize-node-button-/)).toHaveText('Replace part');
  });

  test('Structure tab: DFM overhang check (FORGE-273)', async ({ page }) => {
    await page.goto('/twin?demo=1');
    // The agent chat panel is open by default and overlaps the tree.
    await page.getByRole('button', { name: 'Agent' }).click();
    await page.getByRole('button', { name: 'Structure', exact: true }).click();

    const upperArmRow = page.locator('.tw-structure-row', { hasText: 'upper_arm' });
    await expect(upperArmRow).toBeVisible();
    await expect(upperArmRow.getByTestId(/^dfm-check-button-/)).toBeVisible();

    await upperArmRow.getByTestId(/^dfm-check-button-/).click();
    const panel = page.getByTestId('dfm-overhang-panel');
    await expect(panel).toBeVisible();

    const runButton = panel.getByTestId('run-dfm-check');
    await expect(runButton).toBeDisabled();
    await panel.getByTestId('dfm-mesh-file-input').fill('/workspace/elbow_actuator.inp');
    await expect(runButton).not.toBeDisabled();
    await runButton.click();

    const summary = panel.getByTestId('dfm-result-summary');
    await expect(summary).toBeVisible({ timeout: 10_000 });
    // The demo mesh's two illustrative faces are both perfectly horizontal
    // (normals (0,0,-1)/(0,0,1)), so both are flagged past the 45-degree
    // threshold -- a real, non-canned computation over the mocked mesh.
    await expect(summary).toContainText('2/2 faces flagged');
    await expect(panel.getByTestId('dfm-flagged-faces')).toContainText('Surface1');
    await expect(panel.getByTestId('dfm-flagged-faces')).toContainText('Surface2');
  });

  test('Structure tab: Release for manufacture (FORGE-294)', async ({ page }) => {
    await page.goto('/twin?demo=1');
    // The agent chat panel is open by default and overlaps the tree.
    await page.getByRole('button', { name: 'Agent' }).click();
    await page.getByRole('button', { name: 'Structure', exact: true }).click();

    const upperArmRow = page.locator('.tw-structure-row', { hasText: 'upper_arm' });
    await expect(upperArmRow).toBeVisible();
    await expect(upperArmRow.getByTestId(/^manufacture-release-button-/)).toBeVisible();

    await upperArmRow.getByTestId(/^manufacture-release-button-/).click();
    const panel = page.getByTestId('manufacture-release-panel');
    await expect(panel).toBeVisible();

    // Defaults to 3D print (STL); release triggers a real browser download.
    const select = panel.getByTestId('manufacture-process-select');
    await expect(select).toHaveValue('3d_print');
    const [stlDownload] = await Promise.all([
      page.waitForEvent('download'),
      panel.getByTestId('run-manufacture-release').click(),
    ]);
    expect(stlDownload.suggestedFilename()).toBe('release.stl');
    await expect(panel.getByTestId('manufacture-release-result')).toContainText('STL');

    // Switching to CNC and releasing again downloads a STEP file instead.
    await select.selectOption('cnc');
    const [stepDownload] = await Promise.all([
      page.waitForEvent('download'),
      panel.getByTestId('run-manufacture-release').click(),
    ]);
    expect(stepDownload.suggestedFilename()).toBe('release.step');
    await expect(panel.getByTestId('manufacture-release-result')).toContainText('STEP');
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

  test('Robot viewer: "Use as load case" computes joint loads (FORGE-283)', async ({ page }) => {
    await page.goto('/twin?demo=1');
    // The agent chat panel is open by default and overlaps the tree.
    await page.getByRole('button', { name: 'Agent' }).click();
    await page.getByText('drone-assembly.urdf').click();
    await page.getByRole('button', { name: 'Model', exact: true }).click();

    // The robot loads into the main viewer's Canvas; its one non-fixed
    // joint ("rotor_joint", revolute) gets a slider (MET-747).
    await expect(page.getByLabel('joint rotor_joint')).toBeVisible({ timeout: 15_000 });

    const useAsLoadCase = page.getByRole('button', { name: 'Use as load case' });
    await expect(useAsLoadCase).toBeVisible();
    await useAsLoadCase.click();

    await expect(page.getByText(/Worst joint:/)).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/Reaction force \(N\):/)).toBeVisible();
    await expect(page.getByText(/Reaction moment \(N·mm\):/)).toBeVisible();
  });

  test('Robot viewer: drag-to-pose, presets, and saved poses persist (FORGE-250)', async ({
    page,
  }) => {
    await page.goto('/twin?demo=1');
    // The agent chat panel is open by default and overlaps the tree.
    await page.getByRole('button', { name: 'Agent' }).click();
    await page.getByText('drone-assembly.urdf').click();
    await page.getByRole('button', { name: 'Model', exact: true }).click();

    // The sample robot has one non-fixed joint (revolute, "rotor_joint" --
    // see dashboard/src/lib/sample-workspace.ts) -- FORGE-250's own
    // acceptance criteria names a "link_2"/"joint_2" pair from the real
    // AR4 robot_description used in live validation; the demo sample
    // workspace this e2e suite runs against only has the one joint, so
    // this test exercises the same drag-to-pose mechanism against it.
    const slider = page.getByLabel('joint rotor_joint');
    await expect(slider).toBeVisible({ timeout: 15_000 });
    const initialValue = await slider.inputValue();

    const canvas = page.locator('canvas').first();
    const box = await canvas.boundingBox();
    if (!box) throw new Error('robot viewer canvas not found');
    const cx = box.x + box.width / 2;
    const cy = box.y + box.height / 2;

    // Camera auto-fit framing isn't pixel-exact -- probe a small grid of
    // points around the canvas center for one that actually raycasts onto
    // a link (the hover overlay text appears) instead of guessing a single
    // offset and risking a flaky miss.
    const offsets: [number, number][] = [
      [0, 0], [20, 0], [-20, 0], [0, 20], [0, -20],
      [30, 15], [-30, -15], [40, 0], [-40, 0], [0, 40],
    ];
    let hover: { x: number; y: number } | null = null;
    for (const [dx, dy] of offsets) {
      await page.mouse.move(cx + dx, cy + dy);
      if (await page.getByText(/Hovering:/).isVisible().catch(() => false)) {
        hover = { x: cx + dx, y: cy + dy };
        break;
      }
    }
    expect(hover, 'expected to find a hoverable link on the robot').not.toBeNull();

    await page.screenshot({ path: 'test-results/forge-250-before-drag.png' });

    await page.mouse.move(hover!.x, hover!.y);
    await page.mouse.down();
    await page.mouse.move(hover!.x + 60, hover!.y + 40, { steps: 12 });
    await page.mouse.up();

    await page.screenshot({ path: 'test-results/forge-250-after-drag.png' });

    await expect(async () => {
      expect(await slider.inputValue()).not.toBe(initialValue);
    }).toPass({ timeout: 5_000 });

    // Zero preset animates back to 0.
    await page.getByRole('button', { name: 'Zero', exact: true }).click();
    await expect(async () => {
      expect(Number(await slider.inputValue())).toBeCloseTo(0, 1);
    }).toPass({ timeout: 2_000 });

    // Drag again to a non-zero pose, then save it as a named preset --
    // persisted on the node's metadata and versioned (acceptance criteria).
    await page.mouse.move(hover!.x, hover!.y);
    await page.mouse.down();
    await page.mouse.move(hover!.x + 60, hover!.y + 40, { steps: 12 });
    await page.mouse.up();
    await expect(async () => {
      expect(await slider.inputValue()).not.toBe('0');
    }).toPass({ timeout: 5_000 });
    const savedValue = await slider.inputValue();

    await page.getByLabel('New pose name').fill('Extended');
    await page.getByRole('button', { name: 'Save current pose' }).click();

    // The node detail sidebar's History section (always visible alongside
    // the viewer, independent of which viewer tab is active) shows the new
    // revision -- confirms it's actually persisted+versioned, not just
    // held in client state.
    await expect(page.getByText('Saved pose "Extended"')).toBeVisible({ timeout: 10_000 });

    // Reload: the saved preset survives and reappears as a button, reading
    // back from the node rather than any client-only state.
    await page.reload();
    await page.getByRole('button', { name: 'Agent' }).click();
    await page.getByText('drone-assembly.urdf').click();
    await page.getByRole('button', { name: 'Model', exact: true }).click();
    await expect(page.getByLabel('joint rotor_joint')).toBeVisible({ timeout: 15_000 });

    const extendedPreset = page.getByRole('button', { name: 'Extended' });
    await expect(extendedPreset).toBeVisible();
    await extendedPreset.click();
    await expect(async () => {
      expect(await page.getByLabel('joint rotor_joint').inputValue()).toBe(savedValue);
    }).toPass({ timeout: 2_000 });

    // Physics mode disables drag/presets; toggling it back off restores
    // kinematic control at the last pose rather than resetting it.
    await page.getByTestId('main-viewer-robot-physics-toggle').check();
    await expect(page.getByRole('button', { name: 'Zero', exact: true })).not.toBeVisible();
    await page.getByTestId('main-viewer-robot-physics-toggle').uncheck();
    await expect(page.getByLabel('joint rotor_joint')).toHaveValue(savedValue);
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

  test('coverage heatmap shows the 5 traceability percentages (FORGE-297)', async ({ page }) => {
    await page.goto('/requirements?demo=1');
    const coverage = page.getByTestId('requirements-coverage');
    await expect(coverage).toBeVisible();

    await expect(coverage.getByTestId('coverage-tile-needs_to_requirements')).toContainText(
      '100%',
    );
    await expect(
      coverage.getByTestId('coverage-tile-critical_requirements_to_evidence'),
    ).toContainText('50%');
    // A genuinely empty denominator (no verification_case entities in the
    // sample workspace) reports N/A, not 0% or 100%.
    await expect(coverage.getByTestId('coverage-tile-verification_to_evidence')).toContainText(
      'N/A',
    );
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

    // Sparkline renders alongside the iteration table (FORGE-288).
    await expect(loopSection.getByTestId('design-loop-sparkline')).toBeVisible();

    // Loop health panel shows the real iteration budget, not tokens
    // (FORGE-291).
    const healthPanel = loopSection.getByTestId('loop-health-panel');
    await expect(healthPanel).toBeVisible();
    await expect(healthPanel).toContainText('optimal');
    await expect(healthPanel).toContainText('7 / 60 iterations');
    await expect(loopSection.getByTestId('loop-duplicate-badge')).not.toBeVisible();

    // The converged winner's Decision renders as a card, linked via the
    // real GENERATED_FROM edge (FORGE-289).
    await expect(loopSection.getByTestId('decision-card')).toBeVisible();
    await expect(loopSection.getByTestId('decision-card')).toContainText('Optimised wall thickness');

    // Exactly one converged winner, awaiting approval.
    await expect(loopSection.getByText('awaiting approval')).toBeVisible();
    await expect(loopSection.getByTestId('design-loop-approved-badge')).not.toBeVisible();

    await loopSection.getByTestId('approve-design-loop-button').click();
    await expect(loopSection.getByTestId('design-loop-approved-badge')).toBeVisible();
    await expect(loopSection.getByTestId('approve-design-loop-button')).not.toBeVisible();
  });

  test('resubmitting the same design loop inputs is flagged as a duplicate (FORGE-291)', async ({
    page,
  }) => {
    await page.goto('/requirements?demo=1');
    const loopSection = page.getByTestId('design-loop-section');

    await loopSection.getByTestId('start-design-loop-button').click();
    let form = loopSection.getByTestId('design-loop-form');
    await form.getByLabel('CAD work product id').fill('sample-work-product');
    await form.getByLabel('Load (N)').fill('800');
    await form.getByLabel('Deflection limit (mm)').fill('0.5');
    await form.getByRole('button', { name: 'Run' }).click();
    await expect(form).not.toBeVisible();
    await expect(loopSection.getByTestId('loop-duplicate-badge')).not.toBeVisible();

    await loopSection.getByTestId('start-design-loop-button').click();
    form = loopSection.getByTestId('design-loop-form');
    await form.getByLabel('CAD work product id').fill('sample-work-product');
    await form.getByLabel('Load (N)').fill('800');
    await form.getByLabel('Deflection limit (mm)').fill('0.5');
    await form.getByRole('button', { name: 'Run' }).click();
    await expect(form).not.toBeVisible();

    await expect(loopSection.getByTestId('loop-duplicate-badge')).toBeVisible();
    await expect(loopSection.getByTestId('loop-duplicate-badge')).toContainText(
      'duplicate of an earlier run',
    );
    // Same underlying loop -- no second iteration subtree was created.
    await expect(loopSection.getByTestId('design-loop-iteration-row')).toHaveCount(7);
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

  test('trade study scores recorded options and selecting one records a decision (FORGE-262)', async ({
    page,
  }) => {
    await page.goto('/requirements?demo=1');
    const section = page.getByTestId('trade-study-section');
    await expect(section).toBeVisible();

    const table = section.getByTestId('trade-study-table');
    await expect(table).toBeVisible();
    await expect(section.getByTestId('trade-study-option-column')).toHaveCount(2);

    // Default weights (mass_kg:-1, cost_usd:-0.01, risk:-1, performance:1)
    // already favour the lighter, hollow-tube option (0.4 vs 2.85) --
    // confirm the weighted scores reflect the real recorded
    // criteria_scores, not a placeholder.
    const scores = section.getByTestId('trade-study-weighted-score');
    await expect(scores.nth(0)).toHaveText('0.400');
    await expect(scores.nth(1)).toHaveText('2.850');

    // Editing the mass_kg weight recomputes the score client-side.
    await section.getByTestId('trade-study-weight-mass_kg').fill('-2');
    await expect(scores.nth(1)).toHaveText('1.250');

    // Add a new option via the dashboard form.
    await section.getByTestId('open-add-option-button').click();
    const addForm = section.getByTestId('add-option-form');
    await addForm.getByLabel('Option name').fill('Composite tube');
    await addForm.getByLabel('mass_kg', { exact: true }).fill('1.1');
    await addForm.getByRole('button', { name: 'Add' }).click();
    await expect(addForm).not.toBeVisible();
    await expect(section.getByTestId('trade-study-option-column')).toHaveCount(3);
    await expect(table).toContainText('Composite tube');

    // Select a concept -- records a real Decision.
    await section.getByTestId('trade-study-select-option').selectOption({ label: 'Hollow tube' });
    await section
      .locator('input[placeholder="Why this option, over the others?"]')
      .fill('Best mass/cost balance for the arm link.');
    await section.getByTestId('select-concept-button').click();
    await expect(page.getByText('Concept selected -- recorded as a Decision')).toBeVisible();
  });

  test('release packages: lists the illustrative release and creates a new one with a real diff (FORGE-299)', async ({
    page,
  }) => {
    await page.goto('/requirements?demo=1');
    const section = page.getByTestId('release-packages');
    await expect(section).toBeVisible();

    // The sample workspace seeds one pre-existing release.
    await expect(section).toContainText('v1.0 release candidate');
    await expect(section.getByTestId('release-package-sample-release-v1')).toContainText('First release');

    // Creating a second one diffs against the first -- not "First release".
    await section.getByTestId('release-notes-input').fill('v1.1 hotfix');
    await section.getByTestId('create-release-button').click();
    await expect(section).toContainText('v1.1 hotfix');

    const packages = section.getByTestId(/^release-package-/);
    await expect(packages).toHaveCount(2);
    const newest = section.getByTestId(/^release-package-(?!sample-release-v1)/);
    await expect(newest).not.toContainText('First release');
  });
});

test.describe('Design Assistant', () => {
  test('renders page content', async ({ page }) => {
    await page.goto('/assistant');
    await expect(page.locator('main')).toBeVisible();
  });
});
