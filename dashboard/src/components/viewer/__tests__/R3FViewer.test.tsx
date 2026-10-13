import { describe, it, expect, vi, beforeAll, beforeEach, afterEach } from 'vitest';
import { act, fireEvent } from '@testing-library/react';
import { render } from '../../../test/test-utils';
import { R3FViewer, createWorkplaneTexture } from '../R3FViewer';
import * as THREE from 'three';
import { useTransientTransform } from '../../../store/transient-transform-store';
import { useViewerStore } from '../../../store/viewer-store';

// FORGE-613: the workplane is a canvas-drawn texture, and jsdom has no 2D
// context, so without this the viewer draws no floor and the grid tests below
// would pass for the wrong reason. The stub only has to satisfy the drawing
// calls; nothing asserts on the pixels.
beforeAll(() => {
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({
    clearRect: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    strokeRect: vi.fn(),
    strokeStyle: '',
    lineWidth: 1,
  })) as unknown as typeof HTMLCanvasElement.prototype.getContext;
});

// Captured so tests can invoke the real onPointerMissed handler the mocked
// Canvas would otherwise swallow — it needs WebGL, which jsdom doesn't have.
let capturedOnPointerMissed: (() => void) | undefined;

// A minimal but real-enough fake THREE.Camera — CameraController calls
// camera.position.set(...) and camera.lookAt(...); a plain {} (the old mock)
// throws on the first fit attempt.
const fakeCamera = {
  position: { x: 0, y: 0, z: 0, set: vi.fn() },
  // FORGE-613: fitToModel restores the up vector before lookAt, so a reset is
  // never rolled after a free-orbit tumble.
  up: { set: vi.fn() },
  lookAt: vi.fn(),
};

// Mock @react-three/fiber Canvas — it requires WebGL which jsdom doesn't have
vi.mock('@react-three/fiber', () => ({
  Canvas: ({
    children,
    onPointerMissed,
  }: {
    children: React.ReactNode;
    onPointerMissed?: () => void;
  }) => {
    capturedOnPointerMissed = onPointerMissed;
    return <div data-testid="r3f-canvas">{children}</div>;
  },
  useFrame: vi.fn(),
  useThree: vi.fn(() => ({ scene: {}, camera: fakeCamera, gl: {} })),
}));

let capturedFitToModel: (() => void) | undefined;
let capturedOrbitProps: Record<string, unknown> | undefined;
// Toggled by the MET-622 test to simulate the HDR CDN fetch failing —
// Environment throwing must stay contained by its local ErrorBoundary.
let environmentShouldThrow = false;

vi.mock('@react-three/drei', () => ({
  // FORGE-612: the viewer is a turntable again. Its props are captured so the
  // test below can assert the rotation range, and the arcball is mocked too so
  // a regression back to it is visible rather than silently rendering nothing.
  OrbitControls: (props: Record<string, unknown>) => {
    capturedOrbitProps = props;
    return <div data-testid="orbit-controls" />;
  },
  ArcballControls: () => <div data-testid="arcball-controls" />,
  Environment: () => {
    if (environmentShouldThrow) {
      throw new Error(
        'Could not load studio_small_03_1k.hdr: fetch for "https://raw.githubusercontent.com/pmndrs/drei-assets/..." failed',
      );
    }
    return null;
  },
  GizmoHelper: () => null,
  GizmoViewcube: () => null,
  useGLTF: vi.fn(() => ({
    scene: { traverse: vi.fn(), clone: vi.fn() },
    nodes: {},
    materials: {},
  })),
}));

vi.mock('../../../store/viewer-store', () => ({
  useViewerStore: vi.fn((selector: (s: Record<string, unknown>) => unknown) => {
    const state = {
      glbUrl: null,
      manifest: null,
      selectedMeshName: null,
      hiddenMeshes: new Set(),
      explodeFactor: 0,
      resetCamera: vi.fn(),
      registerCameraReset: vi.fn(),
      modelBounds: null,
      booleanCut: { cutterGlbUrl: null, cutterManifest: null },
      registerOrbitControlsEnabledSetter: vi.fn(),
    };
    return selector(state);
  }),
}));

// The real ErrorBoundary logs via this — silence it so the MET-622 test's
// deliberate failure doesn't spam test output while still exercising the
// real catch behavior.
vi.mock('../../../lib/logger', () => ({
  logger: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() },
}));

vi.mock('../../../store/theme-store', () => ({
  useThemeStore: vi.fn((selector: (s: Record<string, unknown>) => unknown) => {
    const state = { mode: 'light' };
    return selector(state);
  }),
}));

// Out of scope for these tests (deselect wiring, camera fit) — stub so a fake
// manifest doesn't need to satisfy their real prop contracts.
vi.mock('../SceneContents', () => ({ SceneContents: () => null }));
vi.mock('../BooleanCutPanel', () => ({ BooleanCutPanel: () => null }));

describe('R3FViewer', () => {
  it('renders placeholder when no model is loaded', () => {
    const { getByText } = render(<R3FViewer />);
    expect(getByText(/upload a step file/i)).toBeInTheDocument();
  });
});

// A model must be "loaded" (glbUrl + manifest truthy) for the viewer to
// render past its placeholder and mount <GizmoControls />.
describe('R3FViewer — deselecting a rigid group (MET-618)', () => {
  // selectPart (highlight tint / status bar / BOM panel) lives in the SAME
  // store as glbUrl/manifest but is a DIFFERENT piece of selection state than
  // selectGroup (the gizmo, in transient-transform-store) — a live check
  // found the gizmo cleared on deselect but the mesh still read as selected,
  // because only selectGroup(null) was being called. Both must clear.
  let selectPartMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    selectPartMock = vi.fn();
    const fakeState = {
      glbUrl: 'blob:fake',
      manifest: { parts: [] },
      selectedMeshName: null,
      hiddenMeshes: new Set(),
      explodeFactor: 0,
      resetCamera: vi.fn(),
      registerCameraReset: vi.fn(),
      modelBounds: null,
      selectPart: selectPartMock,
      booleanCut: { cutterGlbUrl: null, cutterManifest: null },
      registerOrbitControlsEnabledSetter: vi.fn(),
    };
    vi.mocked(useViewerStore).mockImplementation((selector) =>
      selector(fakeState as unknown as Parameters<typeof selector>[0]),
    );
    useTransientTransform.getState().selectGroup('bracket_v2');
  });

  afterEach(() => {
    useTransientTransform.getState().selectGroup(null);
    capturedOnPointerMissed = undefined;
  });

  it('shows the selected group and clears it (gizmo + mesh highlight) on Escape', () => {
    const { getByText, queryByText } = render(<R3FViewer />);
    expect(getByText('bracket_v2')).toBeInTheDocument();

    act(() => {
      fireEvent.keyDown(window, { key: 'Escape' });
    });

    expect(useTransientTransform.getState().selectedGroup).toBeNull();
    expect(queryByText('bracket_v2')).not.toBeInTheDocument();
    expect(selectPartMock).toHaveBeenCalledWith(null);
  });

  it('clears the selection via the × button, discarding any pending delta', () => {
    useTransientTransform.getState().setDelta([5, 0, 0]);
    const { getByLabelText } = render(<R3FViewer />);

    fireEvent.click(getByLabelText('Deselect'));

    const state = useTransientTransform.getState();
    expect(state.selectedGroup).toBeNull();
    expect(state.delta).toEqual([0, 0, 0]);
    expect(selectPartMock).toHaveBeenCalledWith(null);
  });

  it('clears the selection (gizmo + mesh highlight) when a click misses every mesh (onPointerMissed)', () => {
    render(<R3FViewer />);
    expect(useTransientTransform.getState().selectedGroup).toBe('bracket_v2');

    capturedOnPointerMissed?.();

    expect(useTransientTransform.getState().selectedGroup).toBeNull();
    expect(selectPartMock).toHaveBeenCalledWith(null);
  });

  it('does not deselect while nothing is selected (no-op safety)', () => {
    useTransientTransform.getState().selectGroup(null);
    render(<R3FViewer />);

    // No error, no GizmoControls overlay — Escape/click-miss are no-ops.
    act(() => {
      fireEvent.keyDown(window, { key: 'Escape' });
    });
    capturedOnPointerMissed?.();
    expect(useTransientTransform.getState().selectedGroup).toBeNull();
  });
});

// A model must be "loaded" (glbUrl + manifest truthy) for the viewer to
// render past its placeholder and mount CameraController.
describe('R3FViewer — camera fits the loaded model (MET-620)', () => {
  beforeEach(() => {
    fakeCamera.position.set.mockClear();
    fakeCamera.lookAt.mockClear();
    capturedFitToModel = undefined;
  });

  function mockViewerState(modelBounds: Record<string, unknown> | null) {
    vi.mocked(useViewerStore).mockImplementation((selector) =>
      selector({
        glbUrl: 'blob:fake',
        manifest: { parts: [] },
        selectedMeshName: null,
        hiddenMeshes: new Set(),
        explodeFactor: 0,
        resetCamera: vi.fn(),
        registerCameraReset: (fn: () => void) => {
          capturedFitToModel = fn;
        },
        modelBounds,
        booleanCut: { cutterGlbUrl: null, cutterManifest: null },
      registerOrbitControlsEnabledSetter: vi.fn(),
      } as unknown as Parameters<typeof selector>[0]),
    );
  }

  it('falls back to the generic framing via Reset View when no model bounds are available yet', () => {
    mockViewerState(null);
    render(<R3FViewer />);

    // Nothing has loaded, so the auto-fit-on-load effect never fires — only
    // an explicit Reset View (the registered callback) should frame anything.
    expect(fakeCamera.position.set).not.toHaveBeenCalled();
    capturedFitToModel?.();

    // DEFAULT_BOUNDS: center [0,0,0], radius 36 -> dist 36*2.2=79.2.
    expect(fakeCamera.position.set).toHaveBeenCalled();
    const [x, y, z] = fakeCamera.position.set.mock.calls[0] ?? [];
    expect(x).toBeCloseTo(0.6247 * 79.2, 1);
    expect(y).toBeCloseTo(0.4685 * 79.2, 1);
    expect(z).toBeCloseTo(0.6247 * 79.2, 1);
    expect(fakeCamera.lookAt).toHaveBeenCalledWith(0, 0, 0);
  });

  it('fits the camera to the loaded model — off-center, arbitrary size', () => {
    mockViewerState({ center: [10, 5, -2], radius: 100, groundY: -3 });
    render(<R3FViewer />);

    const dist = 100 * 2.2;
    const [x, y, z] = fakeCamera.position.set.mock.calls[0] ?? [];
    expect(x).toBeCloseTo(10 + 0.6247 * dist, 1);
    expect(y).toBeCloseTo(5 + 0.4685 * dist, 1);
    expect(z).toBeCloseTo(-2 + 0.6247 * dist, 1);
    expect(fakeCamera.lookAt).toHaveBeenCalledWith(10, 5, -2);
  });

  // FORGE-612: the turntable keeps a fixed up vector, so a reset is never
  // rolled by construction and nothing needs restoring. What does need
  // asserting is the rotation range -- see the describe below.
});

// FORGE-613: both camera modes ship, because which one is right is taste. The
// turntable keeps the floor level but stops short of straight up/down (three's
// own Spherical.makeSafe clamps it, whatever the props say); free orbit has no
// pole but lets the view roll. Nothing else asserts the control type, so
// without these a silent swap would pass every other test.
describe('R3FViewer — camera modes and the workplane (FORGE-613)', () => {
  function mockState(over: Record<string, unknown> = {}) {
    vi.mocked(useViewerStore).mockImplementation((selector) =>
      selector({
        glbUrl: 'blob:fake',
        manifest: { parts: [] },
        selectedMeshName: null,
        hiddenMeshes: new Set(),
        explodeFactor: 0,
        resetCamera: vi.fn(),
        registerCameraReset: vi.fn(),
        modelBounds: { center: [10, 5, -2], radius: 100, groundY: -3 },
        booleanCut: { cutterGlbUrl: null, cutterManifest: null },
        registerOrbitControlsEnabledSetter: vi.fn(),
        cameraMode: 'turntable',
        setCameraMode: vi.fn(),
        ...over,
      } as unknown as Parameters<typeof selector>[0]),
    );
  }

  beforeEach(() => {
    capturedOrbitProps = undefined;
    mockState();
  });

  it('defaults to the turntable, not a free-tumbling arcball', () => {
    const { queryByTestId } = render(<R3FViewer />);

    expect(queryByTestId('orbit-controls')).toBeTruthy();
    expect(queryByTestId('arcball-controls')).toBeNull();
  });

  it('switches to the arcball when the camera mode is free', () => {
    mockState({ cameraMode: 'free' });

    const { queryByTestId } = render(<R3FViewer />);

    expect(queryByTestId('arcball-controls')).toBeTruthy();
    expect(queryByTestId('orbit-controls')).toBeNull();
  });

  it('asks the turntable for the full sweep and an unbounded azimuth', () => {
    render(<R3FViewer />);

    // The props do not win -- Spherical.makeSafe() still stops the drag just
    // short of the poles -- but asking for less would narrow it further.
    expect(capturedOrbitProps?.minPolarAngle).toBe(0);
    expect(capturedOrbitProps?.maxPolarAngle).toBeCloseTo(Math.PI, 6);
    expect(capturedOrbitProps?.minAzimuthAngle).toBeUndefined();
    expect(capturedOrbitProps?.maxAzimuthAngle).toBeUndefined();
  });

  it('offers the mode toggle, labelled for where it will take you', () => {
    const setCameraMode = vi.fn();
    mockState({ setCameraMode });

    const { getByLabelText } = render(<R3FViewer />);
    fireEvent.click(getByLabelText('Switch to free orbit'));

    expect(setCameraMode).toHaveBeenCalledWith('free');
  });

  it('draws the workplane the model stands on', () => {
    const { container } = render(<R3FViewer />);

    // One textured plane, not line geometry: a mipmapped texture averages the
    // cells down with distance instead of aliasing into a shimmer, which is
    // what sank both earlier attempts (FORGE-34, FORGE-612).
    expect(container.querySelector('mesh[name="ground-grid"]')).toBeTruthy();
  });

  it('draws no workplane when the model has no bounds, rather than guessing a height', () => {
    // The robot-description path never computes modelBounds. A floor placed
    // from a guess would cut through the robot, so there simply isn't one.
    mockState({ modelBounds: null });

    const { container } = render(<R3FViewer />);

    expect(container.querySelector('mesh[name="ground-grid"]')).toBeNull();
  });
});

// The whole reason the workplane is a texture and not line geometry. Both
// earlier attempts shimmered during an orbit: drei's Grid evaluated its lines
// per fragment with screen-space derivatives (FORGE-34), and GridHelper's
// LineBasicMaterial has no anti-aliasing at all (FORGE-612). A mipmapped,
// anisotropically-filtered texture is what makes a receding floor hold still.
describe('workplane texture filtering (FORGE-613)', () => {
  it('mipmaps and anisotropically filters, which is what stops the shimmer', () => {
    const texture = createWorkplaneTexture('#3a3a3a', '#5a5a5a', 8);

    expect(texture).not.toBeNull();
    expect(texture?.generateMipmaps).toBe(true);
    expect(texture?.minFilter).toBe(THREE.LinearMipmapLinearFilter);
    expect(texture?.anisotropy).toBe(8);
  });

  it('repeats in both directions, so one tile can tile the whole floor', () => {
    const texture = createWorkplaneTexture('#3a3a3a', '#5a5a5a', 1);

    expect(texture?.wrapS).toBe(THREE.RepeatWrapping);
    expect(texture?.wrapT).toBe(THREE.RepeatWrapping);
  });

  it('draws nothing at all when there is no 2D context, rather than a blank plane', () => {
    const getContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = vi.fn(
      () => null,
    ) as unknown as typeof HTMLCanvasElement.prototype.getContext;

    expect(createWorkplaneTexture('#3a3a3a', '#5a5a5a', 1)).toBeNull();

    HTMLCanvasElement.prototype.getContext = getContext;
  });
});

// A failed HDR fetch inside Environment previously had nowhere to go but the
// page-level ErrorBoundary, replacing the entire viewer with "Something went
// wrong" over what's purely cosmetic IBL lighting (MET-622).
describe('R3FViewer — a failed Environment HDR load is contained (MET-622)', () => {
  beforeEach(() => {
    environmentShouldThrow = true;
    vi.mocked(useViewerStore).mockImplementation((selector) =>
      selector({
        glbUrl: 'blob:fake',
        manifest: { parts: [] },
        selectedMeshName: null,
        hiddenMeshes: new Set(),
        explodeFactor: 0,
        resetCamera: vi.fn(),
        registerCameraReset: vi.fn(),
        modelBounds: null,
        booleanCut: { cutterGlbUrl: null, cutterManifest: null },
      registerOrbitControlsEnabledSetter: vi.fn(),
      } as unknown as Parameters<typeof selector>[0]),
    );
  });

  afterEach(() => {
    environmentShouldThrow = false;
  });

  it('keeps the rest of the viewer rendered instead of crashing the whole page', () => {
    const { getByTestId } = render(<R3FViewer />);

    // The Canvas (and everything else inside it — gizmo, controls) rendered
    // fine; only Environment's own local ErrorBoundary caught anything, so
    // nothing bubbled up to replace the page.
    expect(getByTestId('r3f-canvas')).toBeInTheDocument();
  });
});
