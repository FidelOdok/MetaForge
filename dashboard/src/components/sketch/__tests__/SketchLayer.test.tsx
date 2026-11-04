import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, createEvent, fireEvent, render, screen, waitFor } from '../../../test/test-utils';
import { useSketchStore } from '../../../store/sketch-store';

const saveSketch = vi.fn();
const getCurrentSketch = vi.fn();
vi.mock('../../../api/endpoints/sketches', () => ({
  saveSketch: (...args: unknown[]) => saveSketch(...args),
  getCurrentSketch: (...args: unknown[]) => getCurrentSketch(...args),
}));

import { AUTOSAVE_MS, SketchLayer } from '../SketchLayer';

const P = 'proj-layer';
let view = { width: 800, height: 600 };

// jsdom has no PointerEvent and no pointer capture; give it both.
class FakePointerEvent extends MouseEvent {
  pointerId: number;
  pointerType: string;
  pressure: number;
  constructor(type: string, init: PointerEventInit = {}) {
    super(type, init);
    this.pointerId = init.pointerId ?? 1;
    this.pointerType = init.pointerType ?? 'mouse';
    this.pressure = init.pressure ?? 0.5;
  }
}
const originalRect = Element.prototype.getBoundingClientRect;

beforeAll(() => {
  if (!('PointerEvent' in window)) {
    Object.defineProperty(window, 'PointerEvent', { configurable: true, value: FakePointerEvent });
  }
  Object.assign(Element.prototype, {
    setPointerCapture: () => undefined,
    releasePointerCapture: () => undefined,
  });
  Element.prototype.getBoundingClientRect = function rect() {
    return { left: 0, top: 0, x: 0, y: 0, width: view.width, height: view.height, right: view.width, bottom: view.height, toJSON: () => ({}) } as DOMRect;
  };
});

afterAll(() => {
  Element.prototype.getBoundingClientRect = originalRect;
});

beforeEach(() => {
  view = { width: 800, height: 600 };
  window.localStorage.clear();
  useSketchStore.getState().reset();
  saveSketch.mockReset();
  getCurrentSketch.mockReset();
  getCurrentSketch.mockResolvedValue(null);
  saveSketch.mockImplementation(async (_p: string, body: { save_id: string }) => ({
    node_id: 'node-1',
    item_key: 'SKT-SKETCH',
    revision: 1,
    item_ref: 'SKT-SKETCH@1',
    parent_revision: null,
    save_id: body.save_id,
    operation_id: `job:sketch-${body.save_id}`,
    duplicate: false,
    stroke_count: 1,
    authority: 'human_sketch',
    scale: 'unknown',
    plane_state: 'UNASSIGNED',
    content_hash: 'h',
  }));
});

afterEach(() => {
  vi.useRealTimers();
});

const svg = () => screen.getByTestId('sketch-svg');
const doc = () => useSketchStore.getState().doc(P);
const at = (x: number, y: number, extra: PointerEventInit = {}) => ({ clientX: x, clientY: y, pointerId: 1, pointerType: 'mouse', button: 0, ...extra });

function drag(points: [number, number][], extra: PointerEventInit = {}) {
  const [first, ...rest] = points;
  fireEvent.pointerDown(svg(), at(first![0], first![1], extra));
  for (const [x, y] of rest) fireEvent.pointerMove(svg(), at(x, y, extra));
  const last = points[points.length - 1]!;
  fireEvent.pointerUp(svg(), at(last[0], last[1], extra));
}

function tool(name: string) {
  fireEvent.click(screen.getByRole('button', { name }));
}

describe('SketchLayer', () => {
  it('shows Select, Draw, Erase and Undo by default and the shape tools only while drawing', () => {
    render(<SketchLayer projectId={P} />);
    for (const name of ['Select', 'Draw', 'Erase', 'Undo', 'Redo']) {
      expect(screen.getByRole('button', { name })).toBeInTheDocument();
    }
    const shapes = ['Line', 'Arc', 'Circle', 'Rectangle', 'Smooth curve', 'Select a region'];
    for (const name of shapes) expect(screen.queryByRole('button', { name })).toBeNull();

    tool('Draw');
    for (const name of shapes) expect(screen.getByRole('button', { name })).toBeInTheDocument();
    tool('Circle');
    expect(screen.getByRole('button', { name: 'Circle' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Line' })).toBeInTheDocument();

    tool('Erase');
    for (const name of shapes) expect(screen.queryByRole('button', { name })).toBeNull();
    tool('Select');
    for (const name of shapes) expect(screen.queryByRole('button', { name })).toBeNull();
  });

  it('passes pointer input through to the stage in Select mode', () => {
    render(<SketchLayer projectId={P} />);
    expect(screen.getByTestId('sketch-layer')).toHaveClass('is-passive');
    drag([[100, 100], [200, 200]]);
    expect(doc().strokes).toHaveLength(0);
  });

  it('draws a stroke from pointer events in viewport-independent sketch units', () => {
    render(<SketchLayer projectId={P} />);
    tool('Draw');
    drag([[400, 300], [480, 300], [480, 360]]);
    expect(doc().strokes).toHaveLength(1);
    const stroke = doc().strokes[0]!;
    expect(stroke.tool).toBe('pen');
    expect(stroke.points).toEqual([[0, 0], [0.133333, 0], [0.133333, 0.1]]);
    expect(stroke.source).toBe('human');
    expect(svg().querySelector(`[data-stroke-id="${stroke.id}"]`)).not.toBeNull();
    // The live path is cleared once the stroke is committed.
    expect(screen.getByTestId('sketch-live').getAttribute('d')).toBe('');
    expect(screen.getByText('Your sketch · not CAD · scale unknown · no plane yet')).toBeInTheDocument();
  });

  it('records the same points on a smaller viewport', () => {
    view = { width: 400, height: 300 };
    render(<SketchLayer projectId={P} />);
    tool('Draw');
    drag([[200, 150], [240, 150], [240, 180]]);
    expect(doc().strokes[0]!.points).toEqual([[0, 0], [0.133333, 0], [0.133333, 0.1]]);
    expect(doc().viewport).toEqual({ width_px: 400, height_px: 300 });
  });

  it('reads coalesced pointer events and keeps pen pressure', () => {
    render(<SketchLayer projectId={P} />);
    tool('Draw');
    fireEvent.pointerDown(svg(), at(400, 300, { pointerType: 'pen', pressure: 0.3 }));
    const move = createEvent.pointerMove(svg(), at(460, 300, { pointerType: 'pen', pressure: 0.9 }));
    Object.defineProperty(move, 'getCoalescedEvents', {
      value: () => [
        { clientX: 420, clientY: 300, pressure: 0.4 },
        { clientX: 440, clientY: 300, pressure: 0.6 },
        { clientX: 460, clientY: 300, pressure: 0.9 },
      ],
    });
    fireEvent(svg(), move);
    fireEvent.pointerUp(svg(), at(460, 300, { pointerType: 'pen' }));
    const stroke = doc().strokes[0]!;
    expect(stroke.points).toHaveLength(4);
    const expected = [0.3, 0.4, 0.6, 0.9];
    expect(stroke.pressure).toHaveLength(4);
    stroke.pressure!.forEach((v, i) => expect(v).toBeCloseTo(expected[i]!, 5));
  });

  it('draws shapes: a rectangle is a closed five-point outline', () => {
    render(<SketchLayer projectId={P} />);
    tool('Draw');
    tool('Rectangle');
    drag([[400, 300], [450, 320], [460, 360]]);
    const stroke = doc().strokes[0]!;
    expect(stroke.tool).toBe('rectangle');
    expect(stroke.points).toHaveLength(5);
    expect(stroke.points[0]).toEqual(stroke.points[4]);
  });

  it('erases the strokes the eraser passes over, as one undoable step', () => {
    render(<SketchLayer projectId={P} />);
    tool('Draw');
    drag([[100, 100], [300, 100]]);
    drag([[100, 400], [300, 400]]);
    expect(doc().strokes).toHaveLength(2);
    const [top, bottom] = doc().strokes;

    tool('Erase');
    drag([[200, 50], [200, 150]]);
    expect(doc().strokes.map((s) => s.id)).toEqual([bottom!.id]);

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(doc().strokes.map((s) => s.id)).toEqual([top!.id, bottom!.id]);
    fireEvent.click(screen.getByRole('button', { name: 'Redo' }));
    expect(doc().strokes.map((s) => s.id)).toEqual([bottom!.id]);
  });

  it('selects a region and deletes it with the Delete key', () => {
    render(<SketchLayer projectId={P} />);
    tool('Draw');
    drag([[100, 100], [150, 120]]);
    drag([[600, 500], [650, 520]]);
    tool('Select a region');
    drag([[50, 50], [200, 200]]);
    const first = doc().strokes[0]!.id;
    expect(doc().selection).toEqual([first]);
    fireEvent.keyDown(window, { key: 'Delete' });
    expect(doc().strokes).toHaveLength(1);
    expect(doc().strokes[0]!.id).not.toBe(first);
  });

  it('undoes and redoes with Ctrl+Z and Ctrl+Shift+Z, but not while typing elsewhere', () => {
    render(
      <>
        <textarea aria-label="Composer" />
        <SketchLayer projectId={P} />
      </>,
    );
    tool('Draw');
    drag([[100, 100], [150, 120]]);
    fireEvent.keyDown(screen.getByLabelText('Composer'), { key: 'z', ctrlKey: true });
    expect(doc().strokes).toHaveLength(1);

    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });
    expect(doc().strokes).toHaveLength(0);
    fireEvent.keyDown(window, { key: 'Z', ctrlKey: true, shiftKey: true });
    expect(doc().strokes).toHaveLength(1);
  });

  it('Esc cancels the stroke being drawn, then returns to Select', () => {
    render(<SketchLayer projectId={P} />);
    tool('Draw');
    fireEvent.pointerDown(svg(), at(100, 100));
    fireEvent.pointerMove(svg(), at(200, 200));
    expect(screen.getByTestId('sketch-live').getAttribute('d')).not.toBe('');
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.getByTestId('sketch-live').getAttribute('d')).toBe('');
    fireEvent.pointerUp(svg(), at(200, 200));
    expect(doc().strokes).toHaveLength(0);
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(useSketchStore.getState().tool).toBe('select');
  });

  it('saves after an idle following pointer-up, with a save id and an unassigned plane', async () => {
    vi.useFakeTimers();
    render(<SketchLayer projectId={P} />);
    tool('Draw');
    drag([[400, 300], [480, 300]]);
    expect(saveSketch).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(AUTOSAVE_MS - 100);
    });
    expect(saveSketch).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(200);
    });
    expect(saveSketch).toHaveBeenCalledTimes(1);
    const [projectId, body] = saveSketch.mock.calls[0]!;
    expect(projectId).toBe(P);
    expect(body.save_id).toBeTruthy();
    expect(body.plane_state).toBe('UNASSIGNED');
    expect(body.scale).toBe('unknown');
    expect(body.strokes).toHaveLength(1);
    expect(screen.getByTestId('sketch-save-state')).toHaveTextContent('Saved · SKT-SKETCH@1');
  });

  it('saves on Save sketch, and a retry after a failure reuses the save id', async () => {
    saveSketch.mockRejectedValueOnce({ response: { data: { detail: 'gateway down' } } });
    render(<SketchLayer projectId={P} />);
    tool('Draw');
    drag([[400, 300], [480, 300]]);
    fireEvent.click(screen.getByRole('button', { name: /Save sketch/ }));
    await waitFor(() => expect(screen.getByTestId('sketch-save-state')).toHaveTextContent('Not saved: gateway down'));
    fireEvent.click(screen.getByRole('button', { name: /Retry save/ }));
    await waitFor(() => expect(screen.getByTestId('sketch-save-state')).toHaveTextContent('Saved · SKT-SKETCH@1'));
    expect(saveSketch).toHaveBeenCalledTimes(2);
    expect(saveSketch.mock.calls[0]![1].save_id).toBe(saveSketch.mock.calls[1]![1].save_id);
  });

  it('adopts the saved sketch from the gateway when this browser has none', async () => {
    getCurrentSketch.mockResolvedValue({
      node_id: 'n9',
      item_key: 'SKT-SKETCH',
      revision: 3,
      item_ref: 'SKT-SKETCH@3',
      parent_revision: 'SKT-SKETCH@2',
      name: 'Sketch',
      save_id: 's',
      strokes: [{ id: 'remote-1', tool: 'pen', points: [[0, 0], [0.1, 0.1]], width: 2, created_at: 'x', source: 'human' }],
      viewport: null,
      reference_node_id: null,
      authority: 'human_sketch',
      scale: 'unknown',
      plane_state: 'UNASSIGNED',
      created_at: null,
    });
    render(<SketchLayer projectId={P} />);
    await waitFor(() => expect(svg().querySelector('[data-stroke-id="remote-1"]')).not.toBeNull());
    expect(screen.getByTestId('sketch-save-state')).toHaveTextContent('Saved · SKT-SKETCH@3');
  });

  it('shows a chosen project image under the strokes, labelled illustrative', () => {
    const node = { id: 'img-1', name: 'Photo', type: 'work_product', domain: 'concept', status: 'valid', properties: { wp_type: 'image', format: 'png' } };
    render(<SketchLayer projectId={P} referenceNodes={[node as never]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Reference image under the sketch' }));
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'img-1' } });
    expect(screen.getByText('Reference · illustrative')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('slider'), { target: { value: '0.25' } });
    expect(doc().referenceOpacity).toBe(0.25);
  });

  it('keeps input-to-visible-stroke well under 50 ms (measured)', () => {
    render(<SketchLayer projectId={P} />);
    tool('Draw');
    const live = screen.getByTestId('sketch-live');
    fireEvent.pointerDown(svg(), at(10, 10));
    const samples: number[] = [];
    for (let i = 1; i <= 1000; i += 1) {
      const x = 10 + (i % 780);
      const y = 10 + Math.floor(i / 2) % 580;
      const before = live.getAttribute('d')?.length ?? 0;
      const t0 = performance.now();
      fireEvent.pointerMove(svg(), at(x, y));
      const t1 = performance.now();
      if ((live.getAttribute('d')?.length ?? 0) > before) samples.push(t1 - t0);
    }
    const t0 = performance.now();
    fireEvent.pointerUp(svg(), at(10, 10));
    const commitMs = performance.now() - t0;
    samples.sort((a, b) => a - b);
    const p95 = samples[Math.floor(samples.length * 0.95)]!;
    const p50 = samples[Math.floor(samples.length * 0.5)]!;
    console.info(
      `[sketch latency] pointermove -> visible stroke (jsdom): p50 ${p50.toFixed(3)} ms, p95 ${p95.toFixed(3)} ms over ${samples.length} moves; commit of a ${doc().strokes[0]!.points.length}-point stroke ${commitMs.toFixed(2)} ms`,
    );
    expect(samples.length).toBeGreaterThan(900);
    expect(p95).toBeLessThan(50);
  });
});
