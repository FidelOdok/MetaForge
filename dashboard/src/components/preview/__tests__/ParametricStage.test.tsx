import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { act, render, screen, waitFor } from '../../../test/test-utils';
import { definition, validatedCad } from '../../../lib/preview/__tests__/fixtures';
import type { PreviewBuild, PreviewHandle } from '../../../lib/preview/buildPreview';
import type { PreviewDefinition } from '../../../lib/preview/definition';

/**
 * C3D slice 5 in the stage: the L2 preview rebuilt in the browser, Strategy A
 * commits on release, the swap to L3 and the failure state.
 */

const getPreviewDefinition = vi.fn();
const commitParameters = vi.fn();
const getNodeModel = vi.fn();
const retryOperation = vi.fn();
vi.mock('../../../api/endpoints/parametricPreview', () => ({
  getPreviewDefinition: (...a: unknown[]) => getPreviewDefinition(...a),
  commitParameters: (...a: unknown[]) => commitParameters(...a),
}));
vi.mock('../../../api/endpoints/twin', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/twin')>()),
  getNodeModel: (...a: unknown[]) => getNodeModel(...a),
}));
vi.mock('../../../api/endpoints/operations', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/operations')>()),
  retryOperation: (...a: unknown[]) => retryOperation(...a),
}));

// WebGL is not available here: the canvas reports what it was given, and its
// buttons stand in for the drag handle's pointer events.
const canvasProps: { build: PreviewBuild | null; show: string; l3Url: string | null }[] = [];
vi.mock('../ParametricCanvas', () => ({
  ParametricCanvas: (props: {
    build: PreviewBuild | null;
    show: 'l2' | 'l3';
    l3Url: string | null;
    onHandleChange: (h: PreviewHandle, v: number) => void;
    onHandleRelease: (h: PreviewHandle, v: number) => void;
  }) => {
    canvasProps.push({ build: props.build, show: props.show, l3Url: props.l3Url });
    const handle = props.build?.handles[0];
    return (
      <div data-testid="canvas" data-show={props.show} data-l3={props.l3Url ?? ''} data-top={props.build?.box.max[2] ?? ''}>
        {handle && (
          <>
            <button type="button" onClick={() => props.onHandleChange(handle, 6)}>
              drag to 6
            </button>
            <button type="button" onClick={() => props.onHandleChange(handle, 7)}>
              drag to 7
            </button>
            <button type="button" onClick={() => props.onHandleRelease(handle, 7)}>
              release at 7
            </button>
          </>
        )}
      </div>
    );
  },
}));

import { ParametricStage } from '../ParametricStage';
import { useParametricPreview } from '../../../hooks/use-parametric-preview';

function Harness({ variant = 'create' as 'create' | 'engineering', readOnly = false }) {
  const pp = useParametricPreview('proj-1', 'PSK-SLOT');
  return <ParametricStage pp={pp} variant={variant} readOnly={readOnly} />;
}

function queue(...defs: PreviewDefinition[]) {
  let i = 0;
  getPreviewDefinition.mockImplementation(async () => defs[Math.min(i++, defs.length - 1)]);
}

beforeEach(() => {
  getPreviewDefinition.mockReset();
  commitParameters.mockReset();
  getNodeModel.mockReset().mockResolvedValue({ glb_url: '/v1/twin/nodes/x/model.glb' });
  retryOperation.mockReset();
  canvasProps.length = 0;
});

describe('ParametricStage', () => {
  it('a parameter change rebuilds the preview in the browser, with no network call', async () => {
    queue(definition());
    render(<Harness />);
    await waitFor(() => expect(screen.getByTestId('canvas').dataset.top).toBe('5'));
    const calls = getPreviewDefinition.mock.calls.length;
    const field = screen.getByLabelText('Depth (mm)');
    await userEvent.clear(field);
    await userEvent.type(field, '12');
    await waitFor(() => expect(screen.getByTestId('canvas').dataset.top).toBe('12'));
    expect(commitParameters).not.toHaveBeenCalled();
    expect(getPreviewDefinition.mock.calls.length).toBe(calls);
    expect(screen.getByTestId('parametric-status-line')).toHaveTextContent('3D Preview · provisional');
  });

  it('a drag commits once on release, then swaps to the validated L3 model of that definition', async () => {
    const committed = definition({ depth: 7, base_revision: 2, sketch_ref: 'PSK-SLOT@2', definition_hash: 'hash-7' });
    queue(
      definition(),
      { ...committed, build: { job_id: 'j1', status: 'running' } },
      { ...committed, cad: validatedCad('hash-7'), build: { job_id: 'j1', status: 'succeeded' } },
    );
    commitParameters.mockResolvedValue({ build: { status: 'running', job_id: 'j1', operation_id: 'job:j1' } });
    render(<Harness />);
    await screen.findByText('release at 7');
    await userEvent.click(screen.getByText('drag to 6'));
    await userEvent.click(screen.getByText('drag to 7'));
    await waitFor(() => expect(screen.getByTestId('canvas').dataset.top).toBe('7'));
    expect(commitParameters).not.toHaveBeenCalled();
    await userEvent.click(screen.getByText('release at 7'));
    await waitFor(() => expect(commitParameters).toHaveBeenCalledTimes(1));
    expect(commitParameters).toHaveBeenCalledWith(
      'proj-1',
      'PSK-SLOT',
      expect.objectContaining({ base_revision: 1, values: { depth: 7 }, source: 'drag', feature_id: 'feat-extrude-1' }),
    );
    // Polling the twin: the build ends, the validated revision of this definition arrives.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 1700));
    });
    await waitFor(() => expect(screen.getByTestId('parametric-status-line')).toHaveTextContent('Engineering Geometry · exact model available'));
    expect(screen.getByTestId('parametric-stage').dataset.show).toBe('l3');
    await waitFor(() => expect(getNodeModel).toHaveBeenCalledWith('cad-node-2'));
    expect(commitParameters).toHaveBeenCalledTimes(1);
  });

  it('on a failed build keeps the preview and the last valid model, says so, and retries', async () => {
    const lastValid = validatedCad('hash-4', {
      matches_definition: false,
      node_id: 'cad-node-1',
      head_revision: 1,
      last_validated: { revision: 1, node_id: 'cad-node-1', item_ref: 'CAD-SLOT@1', is_head: true },
    });
    queue(definition({ cad: lastValid, build: { job_id: 'j9', status: 'failed', operation_id: 'job:j9' } }));
    retryOperation.mockResolvedValue({ operation_id: 'job:j10', retry_of: 'job:j9', state: 'queued' });
    render(<Harness />);
    await waitFor(() =>
      expect(screen.getByTestId('parametric-status-line')).toHaveTextContent('Preview available · Engineering update needs attention'),
    );
    expect(screen.getByTestId('parametric-status')).toHaveTextContent('Last valid model kept: CAD-SLOT@1');
    expect(screen.getByTestId('parametric-stage').dataset.show).toBe('l2');
    expect(screen.queryByText(/exact model available/i)).toBeNull();
    // The preview and the last valid exact model are both on screen.
    await waitFor(() => expect(getNodeModel).toHaveBeenCalledWith('cad-node-1'));
    await waitFor(() => expect(screen.getByTestId('canvas').dataset.l3).not.toBe(''));
    expect(screen.getByTestId('canvas').dataset.top).toBe('5');
    await userEvent.click(screen.getByRole('button', { name: /retry/i }));
    expect(retryOperation).toHaveBeenCalledWith('proj-1', 'job:j9');
  });

  it('read only (Create Develop), shows the part with no fields, no Apply and no handles', async () => {
    queue(definition());
    render(<Harness readOnly />);
    await waitFor(() => expect(screen.getByTestId('canvas').dataset.top).toBe('5'));
    expect(screen.queryByLabelText('Depth (mm)')).toBeNull();
    expect(screen.queryByTestId('parametric-apply')).toBeNull();
    expect(screen.queryByRole('form', { name: 'Change the part' })).toBeNull();
    expect(screen.getByTestId('parametric-status-line')).toHaveTextContent('3D Preview · provisional');
  });

  it('Engineering shows the comparison values', async () => {
    queue(
      definition({
        cad: validatedCad('hash-5', {
          comparison: {
            outcome: 'discrepancy',
            checks: [
              { check: 'volume', outcome: 'fail', detail: 'preview 4000.00 mm3, exact 4400.00 mm3' },
              { check: 'surface_distance', outcome: 'pass', detail: 'sampled bidirectional distance: largest 0.010 mm' },
            ],
            discrepancies: [{ check: 'volume', outcome: 'fail', detail: '' }],
            tolerance: { abs_mm: 0.25, rel: 0.01, volume_rel: 0.02, linear_mm: 0.45, basis: 'default basis', source: 'default' },
            preview: { op: 'extrude', definition_hash: 'hash-5', volume_mm3: 4000, intended_through_holes: 0 },
            item_ref: 'CAD-SLOT@2',
            compared_at: '',
          },
        }),
      }),
    );
    render(<Harness variant="engineering" />);
    const table = await screen.findByTestId('comparison-table');
    expect(table).toHaveTextContent('Preview versus exact · discrepancy · CAD-SLOT@2');
    expect(table).toHaveTextContent('preview 4000.00 mm3, exact 4400.00 mm3');
    expect(table).toHaveTextContent('within tolerance');
    expect(screen.getByTestId('parametric-build-ms')).toHaveTextContent(/Preview rebuilt in \d+\.\d ms/);
  });
});
