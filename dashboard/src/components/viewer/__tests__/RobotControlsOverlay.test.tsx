import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RobotControlsOverlay } from '../RobotControlsOverlay';
import { useViewerStore } from '../../../store/viewer-store';

vi.mock('../../../api/endpoints/robotLoads', () => ({
  computeJointLoads: vi.fn(async () => ({
    loads: [
      {
        jointName: 'joint0',
        supportedMassKg: 2.0,
        reactionForceN: [0, 0, 19.6],
        reactionMomentNMm: [0, -1962, 0],
      },
    ],
    worstJoint: {
      jointName: 'joint0',
      supportedMassKg: 2.0,
      reactionForceN: [0, 0, 19.6],
      reactionMomentNMm: [0, -1962, 0],
    },
  })),
}));

const mockGetTwinNode = vi.fn();
const mockIterateWorkProduct = vi.fn();
vi.mock('../../../api/endpoints/twin', () => ({
  getTwinNode: (...args: unknown[]) => mockGetTwinNode(...args),
  iterateWorkProduct: (...args: unknown[]) => mockIterateWorkProduct(...args),
}));

function renderWithClient() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <RobotControlsOverlay />
    </QueryClientProvider>,
  );
}

describe('RobotControlsOverlay -- use as load case (FORGE-283)', () => {
  beforeEach(() => {
    mockGetTwinNode.mockReset().mockResolvedValue(undefined);
    mockIterateWorkProduct.mockReset();
    useViewerStore.setState({
      robotJoints: [{ name: 'joint0', lower: -1, upper: 1, initial: 0 }],
      robotJointValues: { joint0: 0 },
      robotPhysicsEnabled: false,
      robotError: null,
      _computeJointLoadChainFn: (jointNames: string[]) => ({
        links: jointNames.map((name) => ({
          name: `${name}_link`,
          com_world_mm: [100, 0, 0] as [number, number, number],
          mass_kg: 2.0,
        })),
        joints: jointNames.map((name) => ({
          name,
          position_world_mm: [0, 0, 0] as [number, number, number],
        })),
      }),
    });
  });

  it('shows the button when joints are present', () => {
    renderWithClient();
    expect(screen.getByRole('button', { name: 'Use as load case' })).toBeTruthy();
  });

  it('computes and displays the worst joint load on click', async () => {
    renderWithClient();
    fireEvent.click(screen.getByRole('button', { name: 'Use as load case' }));

    await waitFor(() => {
      expect(screen.getByText(/Worst joint:/)).toBeTruthy();
    });
    expect(screen.getByText(/Reaction force \(N\):/)).toBeTruthy();
  });

  it('does nothing when there are no joints', () => {
    useViewerStore.setState({ robotJoints: [] });
    renderWithClient();
    expect(screen.queryByRole('button', { name: 'Use as load case' })).toBeNull();
  });
});

describe('RobotControlsOverlay -- pose presets and saved poses (FORGE-250)', () => {
  const rafOriginal = globalThis.requestAnimationFrame;
  const cafOriginal = globalThis.cancelAnimationFrame;

  beforeEach(() => {
    mockGetTwinNode.mockReset().mockResolvedValue(undefined);
    mockIterateWorkProduct.mockReset().mockResolvedValue({
      revision: 1,
      created_at: '2026-01-01T00:00:00Z',
      content_hash: 'abc',
      change_description: 'Saved pose "Extended"',
      metadata_snapshot: {},
    });
    // Collapse the ~600ms ease-in-out animation to a single synchronous
    // frame by handing `tick` a timestamp far past the duration -- t clamps
    // to 1, so the very first (and only) frame lands exactly on target.
    globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) => {
      cb(1_000_000_000);
      return 1;
    }) as typeof requestAnimationFrame;
    globalThis.cancelAnimationFrame = (() => {}) as typeof cancelAnimationFrame;

    useViewerStore.setState({
      robotDescription: { nodeId: 'node-1' },
      robotJoints: [
        { name: 'joint_1', lower: -1, upper: 1, initial: 0 },
        { name: 'joint_2', lower: 0, upper: 4, initial: 0 },
      ],
      robotJointValues: { joint_1: -1, joint_2: 4 },
      robotPhysicsEnabled: false,
      robotError: null,
      robotHoveredJoint: null,
      _computeJointLoadChainFn: null,
    });
  });

  afterEach(() => {
    globalThis.requestAnimationFrame = rafOriginal;
    globalThis.cancelAnimationFrame = cafOriginal;
  });

  it('shows the four built-in presets', () => {
    renderWithClient();
    for (const label of ['Zero', 'Home', 'Min', 'Max']) {
      expect(screen.getByRole('button', { name: label })).toBeTruthy();
    }
  });

  it('clicking Zero animates every joint to 0', async () => {
    renderWithClient();
    fireEvent.click(screen.getByRole('button', { name: 'Zero' }));
    await waitFor(() => {
      expect(useViewerStore.getState().robotJointValues).toEqual({ joint_1: 0, joint_2: 0 });
    });
  });

  it('clicking Home animates every joint to its range midpoint', async () => {
    renderWithClient();
    fireEvent.click(screen.getByRole('button', { name: 'Home' }));
    await waitFor(() => {
      expect(useViewerStore.getState().robotJointValues).toEqual({ joint_1: 0, joint_2: 2 });
    });
  });

  it('shows the hovered joint name when set', () => {
    useViewerStore.setState({ robotHoveredJoint: 'joint_2' });
    renderWithClient();
    expect(screen.getByText('Hovering: joint_2')).toBeTruthy();
  });

  it('does not show a hover line when nothing is hovered', () => {
    renderWithClient();
    expect(screen.queryByText(/Hovering:/)).toBeNull();
  });

  it('renders a saved pose as a button and animates to it on click', async () => {
    mockGetTwinNode.mockResolvedValue({
      id: 'node-1',
      name: 'AR4',
      type: 'work_product',
      domain: 'mechanical',
      status: 'valid',
      properties: {},
      updatedAt: '2026-01-01T00:00:00Z',
      poses: { Extended: { joint_1: 0.5, joint_2: 3 } },
    });
    renderWithClient();

    const presetButton = await screen.findByRole('button', { name: 'Extended' });
    fireEvent.click(presetButton);
    await waitFor(() => {
      expect(useViewerStore.getState().robotJointValues).toEqual({ joint_1: 0.5, joint_2: 3 });
    });
  });

  it('saves the current pose under the typed name, merging with existing saved poses', async () => {
    mockGetTwinNode.mockResolvedValue({
      id: 'node-1',
      name: 'AR4',
      type: 'work_product',
      domain: 'mechanical',
      status: 'valid',
      properties: {},
      updatedAt: '2026-01-01T00:00:00Z',
      poses: { Home: { joint_1: 0, joint_2: 2 } },
    });
    renderWithClient();
    await screen.findByRole('button', { name: 'Home' });

    fireEvent.change(screen.getByLabelText('New pose name'), { target: { value: 'Extended' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save current pose' }));

    await waitFor(() => {
      expect(mockIterateWorkProduct).toHaveBeenCalledWith(
        'node-1',
        'Saved pose "Extended"',
        { poses: { Home: { joint_1: 0, joint_2: 2 }, Extended: { joint_1: -1, joint_2: 4 } } },
      );
    });
  });

  it('disables Save current pose when the name field is empty', () => {
    renderWithClient();
    expect(screen.getByRole('button', { name: 'Save current pose' })).toBeDisabled();
  });
});
