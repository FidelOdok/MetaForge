import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '../../../test/test-utils';
import { useSketchStore } from '../../../store/sketch-store';

vi.mock('../../../api/endpoints/sketches', () => ({
  saveSketch: vi.fn(),
  getCurrentSketch: vi.fn().mockResolvedValue(null),
}));
const getSketchPlane = vi.fn();
const proposeSketchPlane = vi.fn();
vi.mock('../../../api/endpoints/sketchPlanes', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/sketchPlanes')>()),
  getSketchPlane: (...a: unknown[]) => getSketchPlane(...a),
  proposeSketchPlane: (...a: unknown[]) => proposeSketchPlane(...a),
}));

import { SketchLayer } from '../SketchLayer';

const P = 'proj-plane-layer';

const view = (state: string, principal: string | null) => ({
  sketch_key: 'SKT-SKETCH',
  sketch_ref: 'SKT-SKETCH@1',
  plane: {
    plane_id: 'PLN-SKETCH',
    owner_component: null,
    kind: principal ? 'principal' : 'unassigned',
    principal,
    origin: [0, 0, 0],
    u_axis: [1, 0, 0],
    v_axis: [0, 1, 0],
    normal: [0, 0, 1],
    unit: 'unknown',
    scale: null,
    transform: null,
    source_revision: 'SKT-SKETCH@1',
    attachment: null,
    state,
    proposed_by: principal ? 'user' : null,
    confidence: principal ? 1 : 0,
  },
  item_key: principal ? 'PLN-SKETCH' : null,
  revision: principal ? 1 : null,
  item_ref: principal ? 'PLN-SKETCH@1' : null,
  node_id: null,
  parent_revision: null,
  pinned_sketch_ref: null,
  action: null,
  can_undo: false,
  mode: null,
});

function seedSavedSketch() {
  const store = useSketchStore.getState();
  store.ensure(P);
  store.loadRemote(P, {
    strokes: [{ id: 's1', tool: 'pen', points: [[0, 0], [0.1, 0.1]], width: 2, created_at: '2026-11-04T00:00:00Z', source: 'human' }],
    itemKey: 'SKT-SKETCH',
    itemRef: 'SKT-SKETCH@1',
    nodeId: 'n1',
    createdAt: null,
  });
}

beforeEach(() => {
  window.localStorage.clear();
  useSketchStore.getState().reset();
  getSketchPlane.mockReset().mockResolvedValue(view('UNASSIGNED', null));
  proposeSketchPlane.mockReset().mockResolvedValue({
    sketch_key: 'SKT-SKETCH',
    sketch_ref: 'SKT-SKETCH@1',
    candidate: view('PROPOSED', 'XY').plane,
    candidates: [],
    ambiguous: true,
    requires_confirmation: true,
    current: null,
    notes: [],
  });
});

describe('SketchLayer plane prompt', () => {
  it('never asks for a plane before or while drawing', () => {
    render(<SketchLayer projectId={P} />);
    expect(screen.queryByTestId('plane-prompt')).toBeNull();
    expect(screen.queryByRole('button', { name: /Use in 3D/ })).toBeNull();
  });

  it('asks the stage drawer for the plane choice only when the person asks to use a saved sketch in 3D', async () => {
    seedSavedSketch();
    const onRequestPanel = vi.fn();
    render(<SketchLayer projectId={P} onRequestPanel={onRequestPanel} />);
    const use = await screen.findByRole('button', { name: /Use in 3D/ });
    expect(onRequestPanel).not.toHaveBeenCalled();
    fireEvent.click(use);
    expect(onRequestPanel).toHaveBeenCalledWith('plane');
    // The choice opens in the drawer beside the drawing, never over it.
    expect(screen.queryByTestId('plane-prompt')).toBeNull();
    expect(screen.getByText(/no plane yet/)).toBeInTheDocument();
  });

  it('names the plane once one is confirmed, and offers no prompt', async () => {
    getSketchPlane.mockResolvedValue(view('CONFIRMED', 'XZ'));
    seedSavedSketch();
    render(<SketchLayer projectId={P} onRequestPanel={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/on the Front plane/)).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: /Use in 3D/ })).toBeNull();
  });
});
