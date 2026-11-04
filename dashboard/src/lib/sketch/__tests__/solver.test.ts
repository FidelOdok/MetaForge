import { describe, expect, it } from 'vitest';
import { solveSketch } from '../solver';
import { rowRank, solveLinear } from '../solver/linalg';
import { lineLength, type LinePrim, type Primitive, type SketchConstraint, type Vec } from '../model';

function line(id: string, start: Vec, end: Vec): LinePrim {
  return { id, type: 'line', start, end, estimated: true, confidence: 0.9, residual: 0, source_stroke_ids: [id], source_ranges: [] };
}

let n = 0;
function rule(type: SketchConstraint['type'], refs: string[], extra: Partial<SketchConstraint> = {}): SketchConstraint {
  n += 1;
  return { id: `k${n}-${type}`, type, refs, status: 'accepted', source: 'inferred', confidence: 0.9, ...extra };
}

/** A slightly crooked hand-drawn rectangle with its corner and axis rules. */
function rectangle(): { ps: Primitive[]; cs: SketchConstraint[] } {
  const ps = [
    line('t', [0, 0], [0.5, 0.01]),
    line('r', [0.51, 0.0], [0.5, 0.3]),
    line('b', [0.5, 0.31], [0.01, 0.3]),
    line('l', [0.0, 0.3], [0.0, 0.01]),
  ];
  const cs = [
    rule('coincident', ['t.end', 'r.start']),
    rule('coincident', ['r.end', 'b.start']),
    rule('coincident', ['b.end', 'l.start']),
    rule('coincident', ['l.end', 't.start']),
    rule('horizontal', ['t']),
    rule('horizontal', ['b']),
    rule('vertical', ['r']),
    rule('vertical', ['l']),
  ];
  return { ps, cs };
}

describe('linear algebra', () => {
  it('solves a small system and refuses a singular one', () => {
    const x = solveLinear(Float64Array.from([2, 1, 1, 3]), Float64Array.from([3, 5]), 2)!;
    expect(x[0]).toBeCloseTo(0.8, 12);
    expect(x[1]).toBeCloseTo(1.4, 12);
    expect(solveLinear(Float64Array.from([1, 2, 2, 4]), Float64Array.from([1, 2]), 2)).toBeNull();
  });

  it('finds the rank and names the rows a dependent row repeats', () => {
    const rows = [Float64Array.from([1, 0, 0]), Float64Array.from([0, 1, 0]), Float64Array.from([1, 1, 0])];
    const r = rowRank(rows);
    expect(r.rank).toBe(2);
    expect(r.dependent).toEqual([{ row: 2, group: [0, 1, 2] }]);
  });
});

describe('solveSketch', () => {
  it('counts the freedom of a free line', () => {
    const { report } = solveSketch([line('a', [0, 0], [0.4, 0.1])], []);
    expect(report.dof).toBe(4);
    // Move, move, turn are the whole sketch; its length is the one shape freedom.
    expect(report.shape_dof).toBe(1);
    expect(report.status).toBe('under');
    expect(report.message).toBe('1 more dimension or rule would fix the shape and size.');
  });

  it('squares a drawn rectangle and says two dimensions would fix it', () => {
    const { ps, cs } = rectangle();
    const { primitives, report } = solveSketch(ps, cs);
    expect(report.converged).toBe(true);
    expect(report.dof).toBe(4);
    expect(report.shape_dof).toBe(2);
    expect(report.status).toBe('under');
    expect(report.message).toBe('2 more dimensions or rules would fix the shape and size.');
    const [t, r] = primitives as LinePrim[];
    expect(t!.start[1]).toBeCloseTo(t!.end[1], 9);
    expect(r!.start[0]).toBeCloseTo(r!.end[0], 9);
    expect(t!.end[0]).toBeCloseTo(r!.start[0], 9);
    // Under-constrained: nothing moves far from what was drawn.
    expect(Math.abs(t!.end[0] - 0.5)).toBeLessThan(0.02);
  });

  it('is fully constrained with a width and a height', () => {
    const { ps, cs } = rectangle();
    const dims = [rule('length', ['t'], { value: 60, source: 'human_confirmed' }), rule('length', ['r'], { value: 40, source: 'human_confirmed' })];
    const { primitives, report } = solveSketch(ps, [...cs, ...dims], { scale: 1 });
    expect(report.status).toBe('fully');
    expect(report.shape_dof).toBe(0);
    expect(report.dof).toBe(2);
    expect(report.message).toContain('fully fixed');
    expect(lineLength(primitives[0] as LinePrim)).toBeCloseTo(60, 6);
    expect(lineLength(primitives[1] as LinePrim)).toBeCloseTo(40, 6);
  });

  it('explains a conflict and offers to drop or relax', () => {
    const { ps, cs } = rectangle();
    const top = rule('length', ['t'], { value: 0.5, source: 'human_confirmed' });
    const bottom = rule('length', ['b'], { value: 0.3, source: 'human_confirmed' });
    const { primitives, report } = solveSketch(ps, [...cs, top, bottom]);
    expect(report.status).toBe('over');
    expect(report.converged).toBe(false);
    const conflict = report.conflicts[0]!;
    expect(conflict.constraint_ids).toEqual(expect.arrayContaining([top.id, bottom.id]));
    expect(conflict.message).toMatch(/conflict/);
    expect(conflict.message).toContain('Length of Line 1 = 0.5');
    const drops = conflict.choices.filter((c) => c.kind === 'drop').map((c) => c.constraint_id);
    expect(drops).toEqual(expect.arrayContaining([top.id, bottom.id]));
    const relax = conflict.choices.find((c) => c.kind === 'relax' && c.constraint_id === bottom.id)!;
    expect(relax.value).toBeCloseTo(0.5, 6);
    // Conflicting geometry is not moved.
    expect(primitives[0]).toEqual(ps[0]);
  });

  it('reports a rule the others already imply as redundant', () => {
    const { ps, cs } = rectangle();
    const extra = rule('parallel', ['t', 'b']);
    const { report } = solveSketch(ps, [...cs, extra]);
    expect(report.converged).toBe(true);
    expect(report.status).toBe('under');
    expect(report.redundant).toContain(extra.id);
  });

  it('converges from a rough start on arcs and circles', () => {
    const ps: Primitive[] = [
      line('a', [0, 0], [0.4, 0.03]),
      { id: 'c', type: 'arc', center: [0.42, 0.18], radius: 0.17, start_angle: 270, end_angle: 360, estimated: true, confidence: 0.8, residual: 0, source_stroke_ids: [], source_ranges: [] },
      { id: 'h', type: 'circle', center: [0.1, 0.2], radius: 0.05, estimated: true, confidence: 0.8, residual: 0, source_stroke_ids: [], source_ranges: [] },
      { id: 'h2', type: 'circle', center: [0.25, 0.2], radius: 0.06, estimated: true, confidence: 0.8, residual: 0, source_stroke_ids: [], source_ranges: [] },
    ];
    const cs = [
      rule('horizontal', ['a']),
      rule('coincident', ['a.end', 'c.start']),
      rule('tangent', ['a', 'c']),
      rule('radius', ['c'], { value: 0.15, source: 'human_confirmed' }),
      rule('equal', ['h', 'h2']),
      rule('radius', ['h'], { value: 0.04, source: 'human_confirmed' }),
    ];
    const { primitives, report } = solveSketch(ps, cs);
    expect(report.converged).toBe(true);
    expect(report.iterations).toBeLessThan(60);
    expect(report.max_residual).toBeLessThan(1e-7);
    const arcP = primitives[1]!;
    expect(arcP.type === 'arc' && arcP.radius).toBeCloseTo(0.15, 6);
    expect(primitives[3]!.type === 'circle' && (primitives[3] as { radius: number }).radius).toBeCloseTo(0.04, 6);
  });

  it('ignores proposed and rejected rules and reports an unusable one', () => {
    const { ps, cs } = rectangle();
    const proposed = cs.map((c) => ({ ...c, status: 'proposed' as const }));
    const bad = rule('radius', ['t'], { value: 1 });
    const { report, invalid, primitives } = solveSketch(ps, [...proposed, bad]);
    expect(report.dof).toBe(16);
    expect(invalid).toEqual([{ id: bad.id, reason: 't is a line, not a circle or arc' }]);
    expect(primitives).toEqual(ps);
  });
});
