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

  test('load-case dialog: 3D face picker sets the fixed/load node sets', async ({ page }) => {
    await page.goto('/sim?demo=1');

    await page.getByRole('button', { name: 'New load case' }).click();
    await expect(page.getByRole('heading', { name: 'New load case' })).toBeVisible();

    // Typing a mesh file path (the FORGE-277 entry point — what a forge
    // chat generate_mesh turn would have produced) fetches its named faces
    // and renders the pickable 3D view.
    await page.getByLabel(/Mesh file/).fill('/workspace/sample.inp');
    const viewer = page.getByTestId('face-picker-viewer');
    await expect(viewer).toBeVisible({ timeout: 15_000 });
    const canvas = viewer.locator('canvas');
    await expect(canvas).toBeVisible();

    const fixedInput = page.getByLabel(/Fixed node set/);
    const loadInput = page.getByLabel(/Load node set/);
    await expect(fixedInput).toHaveValue('');
    await expect(loadInput).toHaveValue('');

    // "Pick fixed face" is the default mode — click a face patch (center of
    // the canvas, where the camera is framed on the two-face sample box).
    const box = await canvas.boundingBox();
    if (!box) throw new Error('face picker canvas has no bounding box');
    await canvas.click({ position: { x: box.width / 2, y: box.height / 2 } });
    await expect(fixedInput).toHaveValue(/Surface[12]/);

    // Switch to load-face mode and pick the other one; setting a load face
    // also renders its direction arrow (FacePickerViewer's LoadArrow).
    await page.getByRole('button', { name: 'Pick load face' }).click();
    await canvas.click({ position: { x: box.width / 2, y: box.height / 2 } });
    await expect(loadInput).toHaveValue(/Surface[12]/);
  });
});
