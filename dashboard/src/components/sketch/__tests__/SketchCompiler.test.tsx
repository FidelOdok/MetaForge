import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '../../../test/test-utils';
import { useSketchCompilerStore } from '../../../store/sketch-compiler-store';
import type { SketchStroke } from '../../../store/sketch-store';
import { noisy, polyline } from '../../../lib/sketch/__tests__/synthetic';
import type { Vec } from '../../../lib/sketch/model';

const storeInterpretation = vi.fn();
const promoteInterpretation = vi.fn();
const approveParametricSketch = vi.fn();
vi.mock('../../../api/endpoints/sketchCompiler', () => ({
  storeInterpretation: (...a: unknown[]) => storeInterpretation(...a),
  promoteInterpretation: (...a: unknown[]) => promoteInterpretation(...a),
  approveParametricSketch: (...a: unknown[]) => approveParametricSketch(...a),
}));

import { SketchCompiler } from '../SketchCompiler';

const P = 'proj-compile';
// Drawn from the middle of one side, so the shapes are numbered from the next corner:
// Line 1 the right side, Line 2 the far side, Line 3 the left side, Line 4 the near side.
const corners: Vec[] = [[0, -0.2], [0.3, -0.2], [0.3, 0.2], [-0.3, 0.2], [-0.3, -0.2], [0, -0.2]];
const STROKES: SketchStroke[] = [
  { id: 'box', tool: 'pen', points: noisy(polyline(corners, 40), 0.0015, 7), width: 2.5, created_at: '2026-11-04T10:00:00Z', source: 'human' },
];

let saves = 0;

function renderCompiler(props: Partial<Parameters<typeof SketchCompiler>[0]> = {}) {
  return render(
    <div style={{ position: 'relative' }}>
      <SketchCompiler
        projectId={P}
        strokes={STROKES}
        itemKey="SKT-SKETCH"
        savedRevision="SKT-SKETCH@3"
        unsaved={false}
        view={{ width: 800, height: 600 }}
        plane={null}
        {...props}
      />
    </div>,
  );
}

async function makeEditable() {
  fireEvent.click(screen.getByTestId('sketch-make-editable'));
  await waitFor(() => expect(screen.getByText(/Saved · SKI-SKETCH@/)).toBeInTheDocument());
}

beforeEach(() => {
  useSketchCompilerStore.getState().reset();
  saves = 0;
  storeInterpretation.mockReset();
  storeInterpretation.mockImplementation(async (_p: string, _k: string, interp: { interpretation_id: string; plane_id: string | null }) => {
    saves += 1;
    return {
      node_id: `n-${saves}`,
      item_key: 'SKI-SKETCH',
      revision: saves,
      item_ref: `SKI-SKETCH@${saves}`,
      interpretation_id: interp.interpretation_id,
      sketch_ref: 'SKT-SKETCH@3',
      plane_id: interp.plane_id,
      unit: 'unknown',
      operation_id: `job:sketch-compile-${interp.interpretation_id}`,
      duplicate: false,
      primitive_count: 4,
      constraint_count: 10,
      accepted_count: 0,
      unsatisfied: [],
      authority: 'proposal',
    };
  });
  promoteInterpretation.mockReset();
  approveParametricSketch.mockReset();
});

describe('SketchCompiler', () => {
  it('offers Make editable only for a saved sketch', () => {
    renderCompiler({ unsaved: true });
    expect(screen.getByTestId('sketch-make-editable')).toBeDisabled();
  });

  it('fits locally, draws the shapes and stores the proposal with its sketch and plane', async () => {
    renderCompiler({ plane: { plane_id: 'PLN-SKETCH@1', unit: 'mm' } });
    await makeEditable();
    const overlay = screen.getByTestId('sketch-compiler-overlay');
    expect(overlay.querySelectorAll('path.skc-shape:not(.is-construction)').length).toBe(4);
    const [, key, interp] = storeInterpretation.mock.calls[0]!;
    expect(key).toBe('SKT-SKETCH');
    expect(interp.sketch_ref).toBe('SKT-SKETCH@3');
    expect(interp.plane_id).toBe('PLN-SKETCH@1');
    expect(interp.unit).toBe('unknown');
    expect(interp.primitives.every((p: { estimated: boolean }) => p.estimated)).toBe(true);
    expect(screen.getByTestId('sketch-compiler-status')).toHaveTextContent(/more dimensions or rules would fix the shape and size/);
    // Nothing is accepted yet: promotion waits.
    expect(screen.getByTestId('sketch-promote')).toBeDisabled();
  });

  it('accepts a rule from its badge and the likely ones at once, then says what is left', async () => {
    renderCompiler();
    await makeEditable();
    const badge = screen.getAllByRole('button', { name: /^Proposed: Line 2 horizontal/ })[0]!;
    fireEvent.click(badge);
    expect(screen.getByRole('button', { name: /^Accepted: Line 2 horizontal/ })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByTestId('sketch-accept-likely'));
    expect(screen.getByTestId('sketch-compiler-status')).toHaveTextContent('2 more dimensions or rules would fix the shape and size.');
    expect(screen.getByText('Unsaved changes')).toBeInTheDocument();
  });

  it('a typed size is confirmed and re-solves; two sizes fix the shape', async () => {
    renderCompiler();
    await makeEditable();
    fireEvent.click(screen.getByTestId('sketch-accept-likely'));
    const width = screen.getByRole('spinbutton', { name: 'Length of Line 2' });
    fireEvent.change(width, { target: { value: '60' } });
    fireEvent.keyDown(width, { key: 'Enter' });
    expect(within(width.closest('li')!).getByText('confirmed')).toBeInTheDocument();
    expect(screen.getByTestId('sketch-compiler-status')).toHaveTextContent('1 more dimension or rule would fix the shape and size.');
    const height = screen.getByRole('spinbutton', { name: 'Length of Line 1' });
    fireEvent.change(height, { target: { value: '40' } });
    fireEvent.blur(height);
    expect(screen.getByTestId('sketch-compiler-status')).toHaveTextContent(/fully fixed/);
  });

  it('explains a conflict in plain words and fixes it with a choice', async () => {
    renderCompiler();
    await makeEditable();
    fireEvent.click(screen.getByTestId('sketch-accept-likely'));
    const top = screen.getByRole('spinbutton', { name: 'Length of Line 2' });
    fireEvent.change(top, { target: { value: '60' } });
    fireEvent.keyDown(top, { key: 'Enter' });
    const bottom = screen.getByRole('spinbutton', { name: 'Length of Line 4' });
    fireEvent.change(bottom, { target: { value: '50' } });
    fireEvent.keyDown(bottom, { key: 'Enter' });
    expect(screen.getByTestId('sketch-compiler-status')).toHaveTextContent(/These two rules conflict: Length of Line 2 = 60 and Length of Line 4 = 50/);
    expect(screen.getByTestId('sketch-promote')).toBeDisabled();
    fireEvent.click(within(screen.getByTestId('sketch-compiler-conflict')).getByRole('button', { name: 'Change it to 60' }));
    expect(screen.getByTestId('sketch-compiler-status')).not.toHaveTextContent(/conflict/);
  });

  it('undoes, compares with the first fit and restores the last saved version', async () => {
    renderCompiler();
    await makeEditable();
    fireEvent.click(screen.getByTestId('sketch-accept-likely'));
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(screen.getByTestId('sketch-accept-likely')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Redo' }));
    fireEvent.click(screen.getByRole('button', { name: 'Compare with the first fit' }));
    expect(screen.getByTestId('sketch-compiler-overlay').querySelectorAll('path.skc-ghost').length).toBe(4);
    fireEvent.click(screen.getByRole('button', { name: 'Restore the last saved version' }));
    expect(screen.getByText(/Saved · SKI-SKETCH@1/)).toBeInTheDocument();
  });

  it('shows the engineering detail: degrees of freedom, rules and residuals', async () => {
    renderCompiler();
    await makeEditable();
    fireEvent.click(screen.getByRole('button', { name: 'Engineering detail' }));
    const detail = screen.getByTestId('sketch-compiler-detail');
    expect(within(detail).getByText('Degrees of freedom')).toBeInTheDocument();
    expect(within(detail).getByText(/16 \(13 shape and size\)/)).toBeInTheDocument();
  });

  it('promotes, waits for sign-off, and approves', async () => {
    promoteInterpretation.mockResolvedValue({
      node_id: 'psk-1',
      item_ref: 'PSK-SKETCH@1',
      approval_id: 'parametric_sketch:psk-1',
      approved: false,
    });
    approveParametricSketch.mockResolvedValue({ node_id: 'psk-1', approved: true, approved_at: 'now' });
    renderCompiler();
    await makeEditable();
    fireEvent.click(screen.getByTestId('sketch-accept-likely'));
    fireEvent.click(screen.getByTestId('sketch-promote'));
    await waitFor(() => expect(screen.getByTestId('sketch-approve')).toBeInTheDocument());
    // The edited proposal was stored again first, and that revision promoted.
    expect(storeInterpretation).toHaveBeenCalledTimes(2);
    expect(promoteInterpretation.mock.calls[0]![1]).toBe('SKI-SKETCH@2');
    expect(screen.getByText(/Waiting for your sign-off · PSK-SKETCH@1/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('sketch-approve'));
    await waitFor(() => expect(screen.getByTestId('sketch-promoted')).toHaveTextContent('Approved · PSK-SKETCH@1'));
    expect(approveParametricSketch).toHaveBeenCalledWith('psk-1');
  });

  it('shows a refused promotion and keeps the same promote id for a retry', async () => {
    promoteInterpretation.mockRejectedValueOnce({ response: { status: 409, data: { detail: { message: 'accepted rules do not hold on this geometry' } } } });
    renderCompiler();
    await makeEditable();
    fireEvent.click(screen.getByTestId('sketch-accept-likely'));
    fireEvent.click(screen.getByTestId('sketch-promote'));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('accepted rules do not hold on this geometry'));
    promoteInterpretation.mockResolvedValueOnce({ node_id: 'psk-2', item_ref: 'PSK-SKETCH@1', approval_id: 'parametric_sketch:psk-2', approved: false });
    fireEvent.click(screen.getByTestId('sketch-promote'));
    await waitFor(() => expect(screen.getByTestId('sketch-approve')).toBeInTheDocument());
    expect(promoteInterpretation.mock.calls[1]![2]).toBe(promoteInterpretation.mock.calls[0]![2]);
  });

  it('flags a proposal made from an older sketch revision', async () => {
    const view = renderCompiler();
    await makeEditable();
    view.rerender(
      <SketchCompiler projectId={P} strokes={STROKES} itemKey="SKT-SKETCH" savedRevision="SKT-SKETCH@4" unsaved={false} view={{ width: 800, height: 600 }} plane={null} />,
    );
    expect(screen.getByText(/Your sketch changed since this was made/)).toBeInTheDocument();
    expect(screen.getByTestId('sketch-promote')).toBeDisabled();
  });
});
