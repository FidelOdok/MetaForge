import { test, expect, type Route } from '@playwright/test';

/**
 * MET-737 E2E: the assembly export panel's "preview" flow — export a
 * multi-part assembly to URDF, load it as an articulated 3D model, and
 * confirm both the kinematic joint slider and the physics toggle are
 * present and usable.
 *
 * All backend calls are mocked via `page.route()` (no live gateway/
 * cadquery-adapter needed — deterministic, fast, matches this repo's other
 * e2e specs' lack of any live-backend dependency). A minimal two-link,
 * one-revolute-joint URDF + a single-triangle binary STL (same fixture
 * shape the backend's own Python tests use — 80-byte header + uint32
 * triangle count + 50-byte triangle records) stand in for a real export.
 */

const EXPORT_ID = 'e2e-test-export';
const DOWNLOAD_PREFIX = `/api/v1/cad-export/download/${EXPORT_ID}`;

const TEST_URDF = `<?xml version="1.0"?>
<robot name="test_robot">
  <link name="base">
    <visual><geometry><mesh filename="base.stl" /></geometry></visual>
    <inertial>
      <origin xyz="0 0 0" rpy="0 0 0" />
      <mass value="1.0" />
      <inertia ixx="0.01" ixy="0" ixz="0" iyy="0.01" iyz="0" izz="0.01" />
    </inertial>
  </link>
  <link name="arm">
    <visual><geometry><mesh filename="arm.stl" /></geometry></visual>
    <inertial>
      <origin xyz="0 0 0" rpy="0 0 0" />
      <mass value="0.5" />
      <inertia ixx="0.005" ixy="0" ixz="0" iyy="0.005" iyz="0" izz="0.005" />
    </inertial>
  </link>
  <joint name="hip" type="continuous">
    <parent link="base" />
    <child link="arm" />
    <origin xyz="0.05 0 0" rpy="0 0 0" />
    <axis xyz="0 0 1" />
  </joint>
</robot>`;

/** One-triangle binary STL — same fixture shape as
 * tests/unit/test_cadquery_usd_export.py::_make_binary_stl on the backend. */
function makeBinaryStl(): Buffer {
  const header = Buffer.alloc(80);
  const countBuf = Buffer.alloc(4);
  countBuf.writeUInt32LE(1, 0);
  const record = Buffer.alloc(50);
  // normal (0,0,1) at offset 0, three vertices at 12/24/36, 0 attr bytes at 48
  record.writeFloatLE(0, 0);
  record.writeFloatLE(0, 4);
  record.writeFloatLE(1, 8);
  record.writeFloatLE(0, 12);
  record.writeFloatLE(0, 16);
  record.writeFloatLE(0, 20);
  record.writeFloatLE(0.01, 24);
  record.writeFloatLE(0, 28);
  record.writeFloatLE(0, 32);
  record.writeFloatLE(0, 36);
  record.writeFloatLE(0.01, 40);
  record.writeFloatLE(0, 44);
  return Buffer.concat([header, countBuf, record]);
}

async function fulfillJson(route: Route, body: unknown) {
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
}

test.describe('URDF assembly preview (MET-737)', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/v1/projects', (route) =>
      fulfillJson(route, {
        projects: [
          {
            id: 'proj-1',
            name: 'Test Project',
            description: '',
            status: 'active',
            work_products: [],
            agent_count: 0,
            last_updated: new Date().toISOString(),
            created_at: new Date().toISOString(),
          },
        ],
        total: 1,
      }),
    );

    await page.route('**/api/v1/twin/nodes*', (route) =>
      fulfillJson(route, {
        nodes: [
          {
            id: 'node-base',
            name: 'Base Plate.step',
            type: 'work_product',
            domain: 'mechanical',
            status: 'valid',
            properties: { wp_type: 'cad_model' },
            updatedAt: new Date().toISOString(),
          },
          {
            id: 'node-arm',
            name: 'Arm Link.step',
            type: 'work_product',
            domain: 'mechanical',
            status: 'valid',
            properties: { wp_type: 'cad_model' },
            updatedAt: new Date().toISOString(),
          },
        ],
        total: 2,
      }),
    );

    await page.route('**/api/v1/twin/relationships*', (route) => fulfillJson(route, { relationships: [] }));

    await page.route('**/api/v1/cad-export/urdf-assembly', (route) =>
      fulfillJson(route, {
        output_file: { filename: 'test_robot.urdf', download_url: `${DOWNLOAD_PREFIX}/test_robot.urdf` },
        mesh_files: [
          { filename: 'base.stl', download_url: `${DOWNLOAD_PREFIX}/base.stl` },
          { filename: 'arm.stl', download_url: `${DOWNLOAD_PREFIX}/arm.stl` },
        ],
        robot_name: 'test_robot',
        link_names: ['base', 'arm'],
        joint_names: ['hip'],
      }),
    );

    await page.route(`**${DOWNLOAD_PREFIX}/test_robot.urdf`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/xml', body: TEST_URDF }),
    );
    await page.route(`**${DOWNLOAD_PREFIX}/base.stl`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/octet-stream', body: makeBinaryStl() }),
    );
    await page.route(`**${DOWNLOAD_PREFIX}/arm.stl`, (route) =>
      route.fulfill({ status: 200, contentType: 'application/octet-stream', body: makeBinaryStl() }),
    );
  });

  test('exports an assembly, opens the preview, and shows a joint slider + physics toggle', async ({ page }) => {
    await page.goto('/twin');
    await expect(page.locator('main')).toBeVisible();

    // Open the assembly export panel (graph-mode toolbar action — 'graph'
    // is the viewer's default viewMode, so no view-toggle click needed).
    await page.getByRole('button', { name: /ASSEMBLY/ }).click();

    // Add both parts. Scoped by its own "+ Add part…" placeholder option
    // (there's also an unrelated "active project" combobox earlier in the
    // DOM). Its value is always controlled back to "" after each selection,
    // so re-using the same locator for both parts is safe.
    const addPartSelect = page.locator('select').filter({ has: page.locator('option', { hasText: '+ Add part…' }) });
    await addPartSelect.selectOption('node-base');
    await addPartSelect.selectOption('node-arm');

    // Set link names so the joint's base/follower fields resolve.
    const linkNameInputs = page.locator('input[placeholder="link name"]');
    await linkNameInputs.nth(0).fill('base');
    await linkNameInputs.nth(1).fill('arm');

    // Add a joint and fill it in to match the mocked export's expectations.
    await page.getByRole('button', { name: '+ Add joint' }).click();
    await page.locator('input[placeholder="joint name"]').fill('hip');
    await page.locator('input[placeholder="base link"]').fill('base');
    await page.locator('input[placeholder="follower link"]').fill('arm');

    // Trigger the preview (always URDF-based — see UrdfPreviewPanel).
    await page.getByTitle(/Preview \(always URDF-based/).click();

    // The preview panel mounts, fetches the mocked URDF + STL meshes, and
    // renders a real WebGL canvas.
    await expect(page.getByText('Assembly preview')).toBeVisible({ timeout: 15_000 });
    const canvas = page.locator('canvas').last();
    await expect(canvas).toBeVisible({ timeout: 15_000 });

    // The one non-fixed joint ("hip", continuous) gets a slider.
    await expect(page.getByLabel('joint hip')).toBeVisible({ timeout: 15_000 });

    // Physics toggle is present and can be flipped without crashing the page.
    const physicsToggle = page.getByTestId('urdf-preview-physics-toggle');
    await expect(physicsToggle).toBeVisible();
    const pageErrors: string[] = [];
    page.on('pageerror', (err) => pageErrors.push(err.message));
    await physicsToggle.check();
    await page.waitForTimeout(500); // let a few physics steps run
    await physicsToggle.uncheck();
    expect(pageErrors, `unexpected page errors: ${pageErrors.join('; ')}`).toEqual([]);
  });
});
