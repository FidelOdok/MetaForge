import { beforeEach, describe, expect, it, vi } from 'vitest';
import { bracketStrokes, noisy, polyline } from './synthetic';
import type { LinePrim, Vec } from '../model';

const fitCalls = vi.hoisted(() => ({ count: 0 }));
vi.mock('../fit', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../fit')>();
  return {
    ...actual,
    fitSketch: (...args: Parameters<typeof actual.fitSketch>) => {
      fitCalls.count += 1;
      return actual.fitSketch(...args);
    },
  };
});

const { acceptConfident, applyChoice, confirmDimension, interpret, measured, setStatus, setUnit, toDrawn } = await import('../compiler');

function box() {
  const corners: Vec[] = [[-0.3, -0.2], [0.3, -0.2], [0.3, 0.2], [-0.3, 0.2], [-0.3, -0.2]];
  return interpret([{ id: 'box', points: noisy(polyline(corners, 40), 0.0015, 3) }], {
    interpretationId: 'int-1',
    sketchRef: 'SKT-SKETCH@2',
    planeId: 'PLN-7',
  });
}

beforeEach(() => {
  fitCalls.count = 0;
});

describe('interpret', () => {
  it('fits, proposes and solves once, carrying the sketch and plane ids through', () => {
    const s = box();
    expect(fitCalls.count).toBe(1);
    expect(s.sketch_ref).toBe('SKT-SKETCH@2');
    expect(s.plane_id).toBe('PLN-7');
    expect(s.unit).toBe('unknown');
    expect(s.constraints.every((c) => c.status === 'proposed')).toBe(true);
    expect(s.solve?.status).toBe('under');
    expect(s.profiles).toHaveLength(1);
  });
});

describe('editing an interpretation', () => {
  it('accepts the confident rules and re-solves without fitting again', () => {
    const s = acceptConfident(box());
    expect(fitCalls.count).toBe(1);
    expect(s.constraints.some((c) => c.status === 'accepted')).toBe(true);
    expect(s.constraints.filter((c) => c.status === 'accepted').every((c) => c.type !== 'length')).toBe(true);
    expect(s.solve?.converged).toBe(true);
    expect(s.solve?.redundant).toEqual([]);
    expect(s.solve?.shape_dof).toBe(2);
  });

  it('a typed dimension is human_confirmed, sets the scale, and re-solves without re-fitting (RC-03)', () => {
    const accepted = acceptConfident(box());
    const width = accepted.constraints.find((c) => c.type === 'length' && c.refs[0] === accepted.primitives[0]!.id)!;
    const before = accepted.primitives.map((p) => p.id);
    const s = confirmDimension(accepted, width.id, 60);
    expect(fitCalls.count).toBe(1);
    expect(s.primitives.map((p) => p.id)).toEqual(before);
    const confirmed = s.constraints.find((c) => c.id === width.id)!;
    expect(confirmed.source).toBe('human_confirmed');
    expect(confirmed.status).toBe('accepted');
    expect(measured(s, confirmed)).toBeCloseTo(60, 5);
    // The other estimates scaled with it: the box is still about 3:2.
    const height = s.constraints.find((c) => c.type === 'length' && c.refs[0] === s.primitives[1]!.id)!;
    expect(height.source).toBe('estimated');
    expect(height.value!).toBeGreaterThan(35);
    expect(height.value!).toBeLessThan(45);
    // The frame maps the scaled shapes back over the strokes.
    const drawn = toDrawn(s.frame, (s.primitives[0] as LinePrim).start);
    expect(Math.hypot(drawn[0] + 0.3, drawn[1] + 0.2)).toBeLessThan(0.02);
    // The second confirmed dimension only re-solves: no new scale.
    const s2 = confirmDimension(s, height.id, 40);
    expect(s2.frame).toEqual(s.frame);
    expect(s2.solve?.status).toBe('fully');
    expect(measured(s2, s2.constraints.find((c) => c.id === height.id)!)).toBeCloseTo(40, 5);
  });

  it('keeps the unit unknown until a person sets it', () => {
    const s = confirmDimension(acceptConfident(box()), box().constraints.find((c) => c.type === 'length')!.id, 50);
    expect(s.unit).toBe('unknown');
    expect(s.constraints.find((c) => c.type === 'length')!.unit).toBe('unknown');
    const mm = setUnit(s, 'mm');
    expect(mm.constraints.filter((c) => c.type === 'length').every((c) => c.unit === 'mm')).toBe(true);
  });

  it('a conflict offers choices that resolve it', () => {
    let s = acceptConfident(box());
    const lengths = s.constraints.filter((c) => c.type === 'length');
    const top = lengths.find((c) => c.refs[0] === s.primitives[0]!.id)!;
    const bottom = lengths.find((c) => c.refs[0] === s.primitives[2]!.id)!;
    s = confirmDimension(s, top.id, 60);
    s = confirmDimension(s, bottom.id, 50);
    expect(s.solve?.status).toBe('over');
    const conflict = s.solve!.conflicts[0]!;
    const relax = conflict.choices.find((c) => c.kind === 'relax' && c.constraint_id === bottom.id)!;
    expect(relax.value).toBeCloseTo(60, 4);
    const fixed = applyChoice(s, relax);
    expect(fixed.solve?.status).not.toBe('over');
    const dropped = applyChoice(s, conflict.choices.find((c) => c.kind === 'drop')!);
    expect(dropped.solve?.status).not.toBe('over');
  });

  it('rejecting a rule takes it out of the solve', () => {
    const s = acceptConfident(box());
    const h = s.constraints.find((c) => c.type === 'horizontal')!;
    const r = setStatus(s, [h.id], 'rejected');
    expect(r.constraints.find((c) => c.id === h.id)!.status).toBe('rejected');
    expect(r.solve!.dof).toBeGreaterThan(s.solve!.dof);
  });
});

describe('benchmarks (FRS section 12: fit and simple solve within 250 ms)', () => {
  it('fits and solves a bracket of about twenty primitives inside the budget', async () => {
    const { fitSketch } = await import('../fit');
    const strokes = bracketStrokes();
    const fitTimes: number[] = [];
    const solveTimes: number[] = [];
    const editTimes: number[] = [];
    let primitiveCount = 0;
    for (let i = 0; i < 5; i += 1) {
      const t0 = performance.now();
      const fit = fitSketch(strokes);
      fitTimes.push(performance.now() - t0);
      primitiveCount = fit.primitives.length;
      const proposal = interpret(strokes, { interpretationId: 'b', sketchRef: null });
      const t1 = performance.now();
      const s = acceptConfident(proposal);
      solveTimes.push(performance.now() - t1);
      const dim = s.constraints.find((c) => c.type === 'length')!;
      const t2 = performance.now();
      confirmDimension(s, dim.id, 120);
      editTimes.push(performance.now() - t2);
    }
    const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)]!;
    const fitMs = median(fitTimes);
    const solveMs = median(solveTimes);
    const editMs = median(editTimes);
    console.info(
      `sketch compiler benchmark: ${primitiveCount} primitives, fit ${fitMs.toFixed(1)} ms, ` +
        `accept-all solve ${solveMs.toFixed(1)} ms, dimension re-solve ${editMs.toFixed(1)} ms (median of 5)`,
    );
    expect(primitiveCount).toBeGreaterThanOrEqual(16);
    expect(fitMs).toBeLessThan(250);
    expect(solveMs).toBeLessThan(250);
    expect(editMs).toBeLessThan(250);
  });
});
