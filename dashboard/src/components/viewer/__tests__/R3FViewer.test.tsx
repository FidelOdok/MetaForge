import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { act, fireEvent } from '@testing-library/react';
import { render } from '../../../test/test-utils';
import { R3FViewer } from '../R3FViewer';
import { useTransientTransform } from '../../../store/transient-transform-store';
import { useViewerStore } from '../../../store/viewer-store';

// Captured so tests can invoke the real onPointerMissed handler the mocked
// Canvas would otherwise swallow — it needs WebGL, which jsdom doesn't have.
let capturedOnPointerMissed: (() => void) | undefined;

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
  useThree: vi.fn(() => ({ scene: {}, camera: {}, gl: {} })),
}));

vi.mock('@react-three/drei', () => ({
  OrbitControls: () => null,
  Environment: () => null,
  ContactShadows: () => null,
  Grid: () => null,
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
      booleanCut: { cutterGlbUrl: null, cutterManifest: null },
    };
    return selector(state);
  }),
}));

vi.mock('../../../store/theme-store', () => ({
  useThemeStore: vi.fn((selector: (s: Record<string, unknown>) => unknown) => {
    const state = { mode: 'light' };
    return selector(state);
  }),
}));

// Out of scope for these tests (deselect wiring) — stub so a fake manifest
// doesn't need to satisfy their real prop contracts.
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
  beforeEach(() => {
    const fakeState = {
      glbUrl: 'blob:fake',
      manifest: { parts: [] },
      selectedMeshName: null,
      hiddenMeshes: new Set(),
      explodeFactor: 0,
      resetCamera: vi.fn(),
      registerCameraReset: vi.fn(),
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

  it('shows the selected group and clears it on Escape', () => {
    const { getByText, queryByText } = render(<R3FViewer />);
    expect(getByText('bracket_v2')).toBeInTheDocument();

    act(() => {
      fireEvent.keyDown(window, { key: 'Escape' });
    });

    expect(useTransientTransform.getState().selectedGroup).toBeNull();
    expect(queryByText('bracket_v2')).not.toBeInTheDocument();
  });

  it('clears the selection via the × button, discarding any pending delta', () => {
    useTransientTransform.getState().setDelta([5, 0, 0]);
    const { getByLabelText } = render(<R3FViewer />);

    fireEvent.click(getByLabelText('Deselect'));

    const state = useTransientTransform.getState();
    expect(state.selectedGroup).toBeNull();
    expect(state.delta).toEqual([0, 0, 0]);
  });

  it('clears the selection when a click misses every mesh (onPointerMissed)', () => {
    render(<R3FViewer />);
    expect(useTransientTransform.getState().selectedGroup).toBe('bracket_v2');

    capturedOnPointerMissed?.();

    expect(useTransientTransform.getState().selectedGroup).toBeNull();
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
