import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '../../../test/test-utils';

const proposeSketchPlane = vi.fn();
const confirmSketchPlane = vi.fn();
const getSketchPlane = vi.fn();
vi.mock('../../../api/endpoints/sketchPlanes', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/sketchPlanes')>()),
  proposeSketchPlane: (...a: unknown[]) => proposeSketchPlane(...a),
  confirmSketchPlane: (...a: unknown[]) => confirmSketchPlane(...a),
  getSketchPlane: (...a: unknown[]) => getSketchPlane(...a),
}));

import { PlanePrompt } from '../PlanePrompt';

const plane = (principal: 'XY' | 'XZ' | 'YZ', state = 'PROPOSED', proposed_by = 'view') => ({
  plane_id: 'PLN-SKETCH',
  owner_component: null,
  kind: 'principal',
  principal,
  origin: [0, 0, 0],
  u_axis: [1, 0, 0],
  v_axis: [0, 0, 1],
  normal: [0, -1, 0],
  unit: 'unknown',
  scale: null,
  transform: null,
  source_revision: 'SKT-SKETCH@1',
  attachment: null,
  state,
  proposed_by,
  confidence: 0.7,
});

const proposal = (over: Record<string, unknown> = {}) => ({
  sketch_key: 'SKT-SKETCH',
  sketch_ref: 'SKT-SKETCH@1',
  candidate: plane('XZ'),
  candidates: [{ principal: 'XZ', view_name: 'Front', proposed_by: 'view', confidence: 0.7, reason: 'the view is looking at the Front' }],
  ambiguous: false,
  requires_confirmation: true,
  current: null,
  notes: [],
  ...over,
});

beforeEach(() => {
  proposeSketchPlane.mockReset().mockResolvedValue(proposal());
  confirmSketchPlane.mockReset().mockImplementation(async (_p, _k, body: { principal: 'XY' | 'XZ' | 'YZ' }) => ({
    sketch_key: 'SKT-SKETCH',
    sketch_ref: 'SKT-SKETCH@1',
    plane: plane(body.principal, 'CONFIRMED', 'user'),
    item_key: 'PLN-SKETCH',
    revision: 1,
    item_ref: 'PLN-SKETCH@1',
    node_id: 'n1',
    parent_revision: null,
    pinned_sketch_ref: 'SKT-SKETCH@1',
    action: 'confirmed',
    can_undo: true,
    mode: null,
    operation_id: 'job:sketch-plane-x',
    duplicate: false,
    unchanged: false,
    sketch_revision_written: null,
  }));
  getSketchPlane.mockReset();
});

describe('PlanePrompt', () => {
  it('offers exactly the five choices, with Select surface disabled until face picking exists', async () => {
    render(<PlanePrompt projectId="p1" sketchKey="SKT-SKETCH" onDone={() => undefined} />);
    for (const name of ['Front', 'Side', 'Top', 'Select surface', 'Keep as concept']) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: 'Select surface' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Select surface' }).getAttribute('title')).toMatch(/not available yet/);
    await screen.findByTestId('plane-proposal-note');
  });

  it('highlights a clear proposal but places nothing until a choice is made', async () => {
    render(<PlanePrompt projectId="p1" sketchKey="SKT-SKETCH" viewHint="front" onDone={() => undefined} />);
    await screen.findByText(/Suggested: Front/);
    expect(screen.getByRole('button', { name: 'Front' }).className).toMatch(/is-suggested/);
    expect(proposeSketchPlane).toHaveBeenCalledWith('p1', 'SKT-SKETCH', { view_hint: 'front', owner_component: null });
    expect(confirmSketchPlane).not.toHaveBeenCalled();
  });

  it('confirms the explicit choice, even against the proposal', async () => {
    const onDone = vi.fn();
    render(<PlanePrompt projectId="p1" sketchKey="SKT-SKETCH" onDone={onDone} />);
    await screen.findByText(/Suggested: Front/);
    fireEvent.click(screen.getByRole('button', { name: 'Top' }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith('confirmed'));
    const body = confirmSketchPlane.mock.calls[0]![2] as { principal: string; proposed_by: string };
    expect(body.principal).toBe('XY');
    expect(body.proposed_by).toBe('user');
  });

  it('records the proposal source when the person accepts the suggestion', async () => {
    render(<PlanePrompt projectId="p1" sketchKey="SKT-SKETCH" onDone={() => undefined} />);
    await screen.findByText(/Suggested: Front/);
    fireEvent.click(screen.getByRole('button', { name: 'Front' }));
    await waitFor(() => expect(confirmSketchPlane).toHaveBeenCalled());
    expect((confirmSketchPlane.mock.calls[0]![2] as { proposed_by: string }).proposed_by).toBe('view');
  });

  it('says so when the proposal is ambiguous, and suggests nothing', async () => {
    proposeSketchPlane.mockResolvedValue(proposal({ ambiguous: true }));
    render(<PlanePrompt projectId="p1" sketchKey="SKT-SKETCH" onDone={() => undefined} />);
    await screen.findByText(/Not sure which plane fits/);
    expect(document.querySelector('.is-suggested')).toBeNull();
  });

  it('keeps the sketch as a concept without any write', async () => {
    const onDone = vi.fn();
    render(<PlanePrompt projectId="p1" sketchKey="SKT-SKETCH" onDone={onDone} />);
    fireEvent.click(screen.getByRole('button', { name: 'Keep as concept' }));
    expect(onDone).toHaveBeenCalledWith('concept');
    expect(confirmSketchPlane).not.toHaveBeenCalled();
  });
});
