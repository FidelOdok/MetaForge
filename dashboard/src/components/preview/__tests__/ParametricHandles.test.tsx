import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen, waitFor } from '../../../test/test-utils';
import { definition } from '../../../lib/preview/__tests__/fixtures';
import { boxDefinition } from '../../../lib/preview/__tests__/boxFixture';
import type { PreviewBuild, PreviewHandle } from '../../../lib/preview/buildPreview';
import type { PreviewDefinition } from '../../../lib/preview/definition';

/**
 * Drag handles beyond depth and radius: a line's length (re-solved in the
 * browser, committed once on release, refused on a conflict) and the revolve
 * angle gizmo.
 */

const getPreviewDefinition = vi.fn();
const commitParameters = vi.fn();
vi.mock('../../../api/endpoints/parametricPreview', () => ({
  getPreviewDefinition: (...a: unknown[]) => getPreviewDefinition(...a),
  commitParameters: (...a: unknown[]) => commitParameters(...a),
}));
vi.mock('../../../api/endpoints/twin', async (orig) => ({
  ...(await orig<typeof import('../../../api/endpoints/twin')>()),
  getNodeModel: vi.fn().mockResolvedValue({ glb_url: '/x.glb' }),
}));

// No WebGL: the canvas lists its handles, and buttons stand in for pointer moves.
vi.mock('../ParametricCanvas', () => ({
  ParametricCanvas: (props: {
    build: PreviewBuild | null;
    onHandleChange: (h: PreviewHandle, v: number) => void;
    onHandleRelease: (h: PreviewHandle, v: number) => void;
  }) => {
    const b = props.build;
    const width = b ? b.box.max[0] - b.box.min[0] : 0;
    return (
      <div data-testid="canvas" data-width={width.toFixed(3)} data-volume={b ? Math.round(b.volumeMm3) : ''}>
        {b?.handles.map((h) => {
          const name = `${h.kind}:${h.constraintId ?? h.parameter}`;
          return (
            <div key={name} data-testid={`handle-${name}`} data-value={h.value.toFixed(3)} data-arc={h.arc?.length ?? 0}>
              {[45, 50, 180].map((v) => (
                <button key={v} type="button" onClick={() => props.onHandleChange(h, v)}>
                  {`move ${name} to ${v}`}
                </button>
              ))}
              {[45, 50, 180].map((v) => (
                <button key={`r${v}`} type="button" onClick={() => props.onHandleRelease(h, v)}>
                  {`release ${name} at ${v}`}
                </button>
              ))}
            </div>
          );
        })}
      </div>
    );
  },
}));

import { ParametricStage } from '../ParametricStage';
import { useParametricPreview } from '../../../hooks/use-parametric-preview';

function Harness() {
  const pp = useParametricPreview('proj-1', 'PSK-PLATE');
  return <ParametricStage pp={pp} variant="create" />;
}

function serve(def: PreviewDefinition) {
  getPreviewDefinition.mockResolvedValue(def);
}

beforeEach(() => {
  getPreviewDefinition.mockReset();
  commitParameters.mockReset().mockResolvedValue({ build: { status: 'running', job_id: 'j1', operation_id: 'job:j1' } });
});

describe('the line-length handle', () => {
  it('re-solves while dragging, then commits once on release with base_revision and the solved geometry', async () => {
    serve(boxDefinition());
    render(<Harness />);
    await screen.findByTestId('handle-length:L1');
    expect(screen.getByTestId('canvas').dataset.width).toBe('40.000');

    await userEvent.click(screen.getByText('move length:L1 to 45'));
    await userEvent.click(screen.getByText('move length:L1 to 50'));
    // The preview follows the solve: the plate is 50 wide, the handle reads 50.
    await waitFor(() => expect(screen.getByTestId('canvas').dataset.width).toBe('50.000'));
    expect(screen.getByTestId('handle-length:L1').dataset.value).toBe('50.000');
    expect(screen.getByLabelText('Length top (mm)')).toHaveValue(50);
    expect(commitParameters).not.toHaveBeenCalled();

    await userEvent.click(screen.getByText('release length:L1 at 50'));
    await waitFor(() => expect(commitParameters).toHaveBeenCalledTimes(1));
    const [project, key, body] = commitParameters.mock.calls[0]!;
    expect(project).toBe('proj-1');
    expect(key).toBe('PSK-PLATE');
    expect(body).toMatchObject({ base_revision: 1, feature_id: 'feat-extrude-1', dimensions: { L1: 50 }, source: 'drag', values: {} });
    // Sketch space: the solved top line is 50 long, and the joined sides moved with it.
    const top = body.geometry.top as { start: number[]; end: number[] };
    expect(Math.hypot(top.end[0]! - top.start[0]!, top.end[1]! - top.start[1]!)).toBeCloseTo(50, 5);
    expect(Object.keys(body.geometry)).toEqual(expect.arrayContaining(['top']));
    expect(screen.queryByTestId('parametric-conflict')).toBeNull();
  });

  it('refuses a length that conflicts with the accepted rules, says why, and commits nothing', async () => {
    serve(boxDefinition({ bottomLength: 40 }));
    render(<Harness />);
    await screen.findByTestId('handle-length:L1');

    await userEvent.click(screen.getByText('move length:L1 to 50'));
    const conflict = await screen.findByTestId('parametric-conflict');
    expect(conflict).toHaveAttribute('role', 'alert');
    expect(conflict).toHaveTextContent('A length of 50 mm cannot hold with the other accepted rules.');
    expect(conflict).toHaveTextContent(/conflict/);
    // The preview keeps the last good geometry.
    expect(screen.getByTestId('canvas').dataset.width).toBe('40.000');

    await userEvent.click(screen.getByText('release length:L1 at 50'));
    expect(commitParameters).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId('parametric-notes')).toHaveTextContent('cannot hold with the other accepted rules'));
    expect(screen.queryByTestId('parametric-conflict')).toBeNull();
  });
});

describe('the revolve angle handle', () => {
  it('is an arc gizmo next to the numeric field; a drag rebuilds the preview and commits the angle once', async () => {
    serve(
      definition({
        op: 'revolve',
        depth: undefined,
        angle: 360,
        elements: [{ type: 'rectangle', id: 'r', origin: [10, 0], width: 5, height: 20 }],
        parameters: {
          angle: { value: 360, status: 'provided', unit: 'deg', question: '', assumed: false },
          material: { value: 'PLA', status: 'provided', unit: null, question: '', assumed: false },
        },
        handles: [{ kind: 'revolve_angle', parameter: 'angle', axis: 'normal' }],
      }),
    );
    render(<Harness />);
    const handle = await screen.findByTestId('handle-revolve_angle:angle');
    expect(Number(handle.dataset.arc)).toBeGreaterThan(2);
    expect(screen.getByLabelText('Angle (deg)')).toHaveValue(360);
    const full = Number(screen.getByTestId('canvas').dataset.volume);

    await userEvent.click(screen.getByText('move revolve_angle:angle to 180'));
    await waitFor(() => expect(Number(screen.getByTestId('canvas').dataset.volume)).toBeCloseTo(full / 2, -1));
    expect(screen.getByLabelText('Angle (deg)')).toHaveValue(180);
    expect(commitParameters).not.toHaveBeenCalled();

    await userEvent.click(screen.getByText('release revolve_angle:angle at 180'));
    await waitFor(() => expect(commitParameters).toHaveBeenCalledTimes(1));
    expect(commitParameters.mock.calls[0]![2]).toMatchObject({ base_revision: 1, values: { angle: 180 }, source: 'drag' });
  });
});
