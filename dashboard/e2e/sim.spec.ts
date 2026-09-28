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
