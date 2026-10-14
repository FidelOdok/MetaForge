import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as THREE from 'three';
import { render } from '../../../test/test-utils';
import { SceneBody } from '../SceneContents';
import { useViewerStore } from '../../../store/viewer-store';

vi.mock('@react-three/fiber', () => ({
  useFrame: vi.fn(),
  useThree: vi.fn(() => ({ scene: new THREE.Scene(), camera: {}, gl: {} })),
}));

vi.mock('../../../store/viewer-store', () => ({ useViewerStore: vi.fn() }));
vi.mock('../../../store/transient-transform-store', () => ({
  useTransientTransform: vi.fn(() => undefined),
}));

/** A table as the STEP converter delivers one: Z-up, standing in the +octant.
 * Top 1200 x 800 x 18 sitting at z≈731, on legs 722 tall along Z — the exact
 * shape measured in the deployed viewer for FORGE-621. */
function zUpTable() {
  const scene = new THREE.Group();
  const top = new THREE.Mesh(new THREE.BoxGeometry(1200, 800, 18));
  top.name = 'tabletop';
  top.position.set(600, 400, 731);
  scene.add(top);
  for (const [x, y, name] of [
    [20, 20, 'leg_front_left'],
    [1180, 20, 'leg_front_right'],
    [20, 780, 'leg_back_left'],
    [1180, 780, 'leg_back_right'],
  ] as const) {
    const leg = new THREE.Mesh(new THREE.BoxGeometry(40, 40, 722));
    leg.name = name;
    leg.position.set(x, y, 361);
    scene.add(leg);
  }
  return scene;
}

const manifest = { parts: [], meshToNodeMap: {} } as never;

describe('SceneContents — CAD models are turned Y-up (FORGE-621)', () => {
  let bounds: { center: number[]; radius: number; groundY: number } | null;

  beforeEach(() => {
    bounds = null;
    vi.mocked(useViewerStore).mockImplementation((selector) =>
      selector({
        selectedMeshName: null,
        hiddenMeshes: new Set(),
        explodeFactor: 0,
        explodeDirection: 'radial',
        setModelBounds: (b: typeof bounds) => {
          bounds = b;
        },
        focusPart: vi.fn(),
        selectPart: vi.fn(),
        robotJointValues: {},
      } as unknown as Parameters<typeof selector>[0]),
    );
  });

  it('rotates the scene so the model stands up in Y', () => {
    const scene = zUpTable();

    render(<SceneBody scene={scene} manifest={manifest} />);

    // -90° about X is the same correction the URDF path has always applied.
    expect(scene.rotation.x).toBeCloseTo(-Math.PI / 2, 6);
  });

  it('reports a floor the model actually stands on, not its depth', () => {
    const scene = zUpTable();

    render(<SceneBody scene={scene} manifest={manifest} />);

    expect(bounds).not.toBeNull();
    // Before the fix groundY came out at 0 — the table's 800mm DEPTH read as
    // its height, which is how the workplane ended up cutting through it.
    // Rotated, the feet sit at y = 0 and the top is 740mm above them.
    const box = new THREE.Box3().setFromObject(scene);
    expect(bounds!.groundY).toBeCloseTo(box.min.y, 3);
    expect(box.max.y - box.min.y).toBeCloseTo(740, 0);
    // Upright, not upside down: the top is at the top. -90 about X maps
    // (x,y,z) -> (x,z,-y), so the top's z=731 becomes y=731; +90 would put it
    // at -731, under the floor.
    const top = scene.getObjectByName('tabletop')!;
    expect(top.getWorldPosition(new THREE.Vector3()).y).toBeCloseTo(731, 0);
  });

  it('leaves the height on Y and the footprint on X/Z', () => {
    const scene = zUpTable();

    render(<SceneBody scene={scene} manifest={manifest} />);

    const size = new THREE.Box3().setFromObject(scene).getSize(new THREE.Vector3());
    // 1200 x 800 footprint, 740 tall: the depth is on Z now, not Y.
    expect(size.x).toBeCloseTo(1200, 0);
    expect(size.y).toBeCloseTo(740, 0);
    expect(size.z).toBeCloseTo(800, 0);
  });

  it('does not compound the rotation when the scene re-renders', () => {
    const scene = zUpTable();

    const { rerender } = render(<SceneBody scene={scene} manifest={manifest} />);
    rerender(<SceneBody scene={scene} manifest={manifest} />);
    rerender(<SceneBody scene={scene} manifest={manifest} />);

    expect(scene.rotation.x).toBeCloseTo(-Math.PI / 2, 6);
  });
});
