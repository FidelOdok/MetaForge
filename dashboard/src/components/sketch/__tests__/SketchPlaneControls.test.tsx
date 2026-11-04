import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '../../../test/test-utils';
import { useSketchPlaneUi } from '../../../hooks/use-sketch-plane';

const previewReassign = vi.fn();
const reassignSketchPlane = vi.fn();
const undoSketchPlane = vi.fn();
const confirmSketchPlane = vi.fn();
vi.mock('../../../api/endpoints/sketchPlanes', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/sketchPlanes')>()),
  previewReassign: (...a: unknown[]) => previewReassign(...a),
  reassignSketchPlane: (...a: unknown[]) => reassignSketchPlane(...a),
  undoSketchPlane: (...a: unknown[]) => undoSketchPlane(...a),
  confirmSketchPlane: (...a: unknown[]) => confirmSketchPlane(...a),
  proposeSketchPlane: vi.fn().mockResolvedValue(null),
  getAttachableGeometry: vi.fn().mockResolvedValue({ project_id: 'p1', items: [], reason: null }),
}));

import { SketchPlaneControls } from '../SketchPlaneControls';
import type { SketchPlaneView } from '../../../api/endpoints/sketchPlanes';

const front = {
  plane_id: 'PLN-SKETCH',
  owner_component: null,
  kind: 'principal' as const,
  principal: 'XZ' as const,
  origin: [0, 0, 0] as [number, number, number],
  u_axis: [1, 0, 0] as [number, number, number],
  v_axis: [0, 0, 1] as [number, number, number],
  normal: [0, -1, 0] as [number, number, number],
  unit: 'unknown' as const,
  scale: null,
  transform: null,
  source_revision: 'SKT-SKETCH@1',
  attachment: null,
  state: 'CONFIRMED' as const,
  proposed_by: 'user' as const,
  confidence: 1,
};

const view = (over: Partial<SketchPlaneView> = {}): SketchPlaneView => ({
  sketch_key: 'SKT-SKETCH',
  sketch_ref: 'SKT-SKETCH@1',
  plane: front,
  item_key: 'PLN-SKETCH',
  revision: 1,
  item_ref: 'PLN-SKETCH@1',
  node_id: 'n1',
  parent_revision: null,
  pinned_sketch_ref: 'SKT-SKETCH@1',
  action: 'confirmed',
  can_undo: true,
  mode: null,
  ...over,
});

const preview = {
  sketch_key: 'SKT-SKETCH',
  sketch_ref: 'SKT-SKETCH@1',
  mode: 'project_to_target',
  source: front,
  target: { ...front, principal: 'XY', v_axis: [0, 1, 0], normal: [0, 0, 1] },
  strokes: [{ id: 's1', points: [[0.1, 0]], off_plane: 0.4 }],
  max_off_plane: 0.4,
  exact: false,
  rewrites_strokes: true,
  committable: true,
  warnings: ['Points move up to 0.4 units to land on the new plane.'],
};

beforeEach(() => {
  useSketchPlaneUi.setState({ preview: null, normalToSketch: null, viewHint: null, showGrid: true, showAxes: true });
  previewReassign.mockReset().mockResolvedValue(preview);
  reassignSketchPlane.mockReset().mockResolvedValue({ ...view({ revision: 2, item_ref: 'PLN-SKETCH@2' }), operation_id: 'o', duplicate: false, unchanged: false, sketch_revision_written: 'SKT-SKETCH@2' });
  undoSketchPlane.mockReset().mockResolvedValue({ ...view({ revision: 3 }), operation_id: 'o', duplicate: false, unchanged: false, sketch_revision_written: null });
});

describe('SketchPlaneControls', () => {
  it('shows the active plane, its axes, unit, state and the assumed scale', () => {
    render(<SketchPlaneControls projectId="p1" sketchKey="SKT-SKETCH" view={view()} modelRadius={20} />);
    expect(screen.getByTestId('sketch-plane-name')).toHaveTextContent('Front (XZ)');
    expect(screen.getByTestId('sketch-plane-state')).toHaveTextContent('CONFIRMED');
    expect(screen.getByTestId('sketch-plane-axes')).toHaveTextContent('N 0,-1,0');
    expect(screen.getByTestId('sketch-plane-unit')).toHaveTextContent('Unit: unknown');
    expect(screen.getByText(/assumed size/)).toBeInTheDocument();
    expect(screen.getByLabelText('Grid')).toBeChecked();
  });

  it('turns the camera to the sketch without any plane call', () => {
    const turn = vi.fn();
    useSketchPlaneUi.setState({ normalToSketch: turn });
    render(<SketchPlaneControls projectId="p1" sketchKey="SKT-SKETCH" view={view()} />);
    fireEvent.click(screen.getByRole('button', { name: /Normal to sketch/ }));
    expect(turn).toHaveBeenCalledTimes(1);
    expect(previewReassign).not.toHaveBeenCalled();
    expect(reassignSketchPlane).not.toHaveBeenCalled();
  });

  it('previews a reassign in the chosen mode, then applies it', async () => {
    render(<SketchPlaneControls projectId="p1" sketchKey="SKT-SKETCH" view={view()} />);
    fireEvent.change(screen.getByLabelText('New plane'), { target: { value: 'XY' } });
    fireEvent.change(screen.getByLabelText('Reassign mode'), { target: { value: 'project_to_target' } });
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await screen.findByTestId('sketch-plane-preview');
    expect(previewReassign.mock.calls[0]![2]).toMatchObject({ target: 'XY', mode: 'project_to_target' });
    expect(useSketchPlaneUi.getState().preview).not.toBeNull();
    expect(reassignSketchPlane).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(reassignSketchPlane).toHaveBeenCalled());
    expect(reassignSketchPlane.mock.calls[0]![2]).toMatchObject({ target: 'XY', mode: 'project_to_target', base_revision: 'PLN-SKETCH@1' });
    await waitFor(() => expect(useSketchPlaneUi.getState().preview).toBeNull());
  });

  it('refuses to apply a preview that cannot be committed', async () => {
    previewReassign.mockResolvedValue({ ...preview, committable: false, mode: 'preserve_world' });
    render(<SketchPlaneControls projectId="p1" sketchKey="SKT-SKETCH" view={view()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Preview' }));
    await screen.findByTestId('sketch-plane-preview');
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled();
  });

  it('undoes the last change', async () => {
    render(<SketchPlaneControls projectId="p1" sketchKey="SKT-SKETCH" view={view()} />);
    fireEvent.click(screen.getByRole('button', { name: /Undo/ }));
    await waitFor(() => expect(undoSketchPlane).toHaveBeenCalled());
  });

  it('offers to choose a plane for an unplaced sketch', () => {
    render(
      <SketchPlaneControls
        projectId="p1"
        sketchKey="SKT-SKETCH"
        view={view({ plane: { ...front, kind: 'unassigned', principal: null, state: 'UNASSIGNED' }, can_undo: false })}
      />,
    );
    expect(screen.getByTestId('sketch-plane-name')).toHaveTextContent('No plane');
    expect(screen.queryByRole('button', { name: /Normal to sketch/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Choose a plane' }));
    expect(screen.getByTestId('plane-prompt')).toBeInTheDocument();
  });
});
