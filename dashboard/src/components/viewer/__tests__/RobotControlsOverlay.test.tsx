import { describe, expect, it, vi, beforeEach } from 'vitest';
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
