import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent } from '@testing-library/react';
import { render } from '../../../test/test-utils';
import { R3FViewer } from '../R3FViewer';
import { useTransientTransform } from '../../../store/transient-transform-store';
import { useViewerStore } from '../../../store/viewer-store';

// Captured so tests can invoke the real onPointerMissed handler the mocked
// Canvas would otherwise swallow — it needs WebGL, which jsdom doesn't have.
let capturedOnPointerMissed: (() => void) | undefined;

// A minimal but real-enough fake THREE.Camera — CameraController calls
// camera.position.set(...) and camera.lookAt(...); a plain {} (the old mock)
// throws on the first fit attempt.
const fakeCamera = {
  position: { x: 0, y: 0, z: 0, set: vi.fn() },
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

// Captures the last props Grid received, so tests can assert on its sizing
// without a real WebGL renderer.
let capturedGridProps: Record<string, unknown> | undefined;
let capturedFitToModel: (() => void) | undefined;
// Toggled by the MET-622 test to simulate the HDR CDN fetch failing —
// Environment throwing must stay contained by its local ErrorBoundary.
let environmentShouldThrow = false;

vi.mock('@react-three/drei', () => ({
  OrbitControls: () => null,
  Environment: () => {
    if (environmentShouldThrow) {
      throw new Error(
        'Could not load studio_small_03_1k.hdr: fetch for "https://raw.githubusercontent.com/pmndrs/drei-assets/..." failed',
      );
    }
    return null;
  },
  Grid: (props: Record<string, unknown>) => {
    capturedGridProps = props;
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
// render past its placeholder and mount CameraController/Grid.
describe('R3FViewer — camera and grid fit the loaded model (MET-620)', () => {
  beforeEach(() => {
    fakeCamera.position.set.mockClear();
    fakeCamera.lookAt.mockClear();
    capturedGridProps = undefined;
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

  it('sizes and positions the grid to the model instead of a fixed 200 units', () => {
    mockViewerState({ center: [10, 5, -2], radius: 100, groundY: -3 });
    render(<R3FViewer />);

    // gridSize = max(20, radius*6) = 600; groundY = model.groundY - 0.5.
    expect(capturedGridProps?.args).toEqual([600, 600]);
    expect(capturedGridProps?.position).toEqual([10, -3.5, -2]);
    expect(capturedGridProps?.fadeDistance).toBe(600);
    // cellSize/sectionSize scale by radius/36.
    expect(capturedGridProps?.cellSize).toBeCloseTo(5 * (100 / 36), 5);
    expect(capturedGridProps?.sectionSize).toBeCloseTo(25 * (100 / 36), 5);
  });

  it('uses the generic 200-ish default grid before any model bounds exist', () => {
    mockViewerState(null);
    render(<R3FViewer />);

    // gridSize = max(20, 36*6) = 216 — close to the old fixed 200, unchanged
    // "nothing loaded yet" look.
    expect(capturedGridProps?.args).toEqual([216, 216]);
    expect(capturedGridProps?.position).toEqual([0, -0.5, 0]);
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
      } as unknown as Parameters<typeof selector>[0]),
    );
  });

  afterEach(() => {
    environmentShouldThrow = false;
  });

  it('keeps the rest of the viewer rendered instead of crashing the whole page', () => {
    const { getByTestId } = render(<R3FViewer />);

    // The Canvas (and everything else inside it — Grid, gizmo, controls)
    // rendered fine; only Environment's own local ErrorBoundary caught
    // anything, so nothing bubbled up to replace the page.
    expect(getByTestId('r3f-canvas')).toBeInTheDocument();
  });
});
