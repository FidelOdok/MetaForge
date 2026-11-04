import { describe, expect, it } from 'vitest';
import { inferConstraints } from '../constraints/infer';
import { solveSketch } from '../solver';
import type { ArcPrim, CirclePrim, LinePrim, Primitive, SketchConstraint, Vec } from '../model';

const TOL = 0.01;

function line(id: string, start: Vec, end: Vec): LinePrim {
  return { id, type: 'line', start, end, estimated: true, confidence: 0.9, residual: 0, source_stroke_ids: [id], source_ranges: [] };
}

function circle(id: string, center: Vec, radius: number): CirclePrim {
  return { id, type: 'circle', center, radius, estimated: true, confidence: 0.9, residual: 0, source_stroke_ids: [id], source_ranges: [] };
}

function arc(id: string, center: Vec, radius: number, start: number, end: number): ArcPrim {
  return {
    id,
    type: 'arc',
    center,
    radius,
    start_angle: start,
    end_angle: end,
    estimated: true,
    confidence: 0.9,
    residual: 0,
    source_stroke_ids: [id],
    source_ranges: [],
  };
}

const of = (cs: SketchConstraint[], type: string) => cs.filter((c) => c.type === type);
const infer = (ps: Primitive[]) => inferConstraints(ps, { tolerance: TOL });

describe('constraint inference', () => {
  it('proposes coincident ends that nearly meet, and nothing for ends far apart', () => {
    const { constraints } = infer([line('a', [0, 0], [0.4, 0.003]), line('b', [0.405, 0.004], [0.5, 0.3]), line('c', [0.9, 0.9], [0.95, 0.6])]);
    const co = of(constraints, 'coincident');
    expect(co).toHaveLength(1);
    expect(co[0]!.refs).toEqual(['a.end', 'b.start']);
    expect(co[0]!.status).toBe('proposed');
    expect(co[0]!.source).toBe('inferred');
    expect(co[0]!.confidence).toBeGreaterThan(0.5);
  });

  it('proposes horizontal and vertical for nearly axis-aligned lines', () => {
    const { constraints } = infer([line('h', [0, 0], [0.5, 0.02]), line('v', [0.7, 0], [0.71, 0.4]), line('d', [0, 0.5], [0.3, 0.8])]);
    expect(of(constraints, 'horizontal').map((c) => c.refs[0])).toEqual(['h']);
    expect(of(constraints, 'vertical').map((c) => c.refs[0])).toEqual(['v']);
  });

  it('links a rotated rectangle by parallel and perpendicular without redundancy', () => {
    const a = (30 * Math.PI) / 180;
    const u: Vec = [Math.cos(a), Math.sin(a)];
    const v: Vec = [-Math.sin(a), Math.cos(a)];
    const p = (s: number, t: number): Vec => [s * u[0] + t * v[0], s * u[1] + t * v[1]];
    const rect = [line('l1', p(0, 0), p(0.5, 0)), line('l2', p(0.5, 0), p(0.5, 0.3)), line('l3', p(0.5, 0.3), p(0, 0.3)), line('l4', p(0, 0.3), p(0, 0))];
    const { constraints } = infer(rect);
    expect(of(constraints, 'parallel')).toHaveLength(2);
    expect(of(constraints, 'perpendicular')).toHaveLength(1);
    const accepted = constraints.filter((c) => ['coincident', 'parallel', 'perpendicular'].includes(c.type)).map((c) => ({ ...c, status: 'accepted' as const }));
    const { report } = solveSketch(rect, accepted);
    expect(report.converged).toBe(true);
    expect(report.redundant).toEqual([]);
    // A rectangle with free size and position: width and height are what is left.
    expect(report.shape_dof).toBe(2);
  });

  it('proposes tangent for a line meeting an arc smoothly, and for two arcs', () => {
    const r = 0.2;
    const { constraints } = infer([
      line('l', [-0.2, 0.3], [-0.2, 0]),
      arc('a', [0, 0], r, 0, 180),
      arc('b', [0.4, 0], r, 180, 360),
    ]);
    const t = of(constraints, 'tangent');
    expect(t.map((c) => c.refs)).toEqual(
      expect.arrayContaining([
        ['l', 'a'],
        ['a', 'b'],
      ]),
    );
    expect(t.find((c) => c.refs[1] === 'b')!.variant).toBe('external');
  });

  it('proposes tangent for a line just touching a circle', () => {
    const { constraints } = infer([circle('c', [0, 0], 0.2), line('l', [-0.3, 0.203], [0.3, 0.203])]);
    expect(of(constraints, 'tangent').map((c) => c.refs)).toEqual([['l', 'c']]);
  });

  it('proposes equal length and equal radius', () => {
    const { constraints } = infer([
      line('a', [0, 0], [0.3, 0.2]),
      line('b', [0.5, 0.5], [0.5 + 0.3 * 1.02, 0.5 - 0.2 * 1.02]),
      line('c', [0, 0.8], [0.1, 0.8]),
      circle('c1', [0, -0.5], 0.05),
      circle('c2', [0.3, -0.5], 0.051),
    ]);
    const eq = of(constraints, 'equal').map((c) => c.refs);
    expect(eq).toEqual(expect.arrayContaining([['a', 'b'], ['c1', 'c2']]));
    expect(eq.some((r) => r.includes('c'))).toBe(false);
  });

  it('proposes concentric circles', () => {
    const { constraints } = infer([circle('o', [0.1, 0.1], 0.2), circle('i', [0.102, 0.099], 0.1)]);
    expect(of(constraints, 'concentric').map((c) => c.refs)).toEqual([['o', 'i']]);
  });

  it('proposes symmetry about an obvious centre line, with a construction axis', () => {
    // A trapezoid symmetric about x = 0.
    const ps = [line('b', [-0.4, 0.3], [0.4, 0.3]), line('r', [0.4, 0.3], [0.2, -0.2]), line('t', [0.2, -0.2], [-0.2, -0.2]), line('l', [-0.2, -0.2], [-0.4, 0.3])];
    const { constraints, construction } = infer(ps);
    expect(construction.map((p) => p.id)).toEqual(['axis-v']);
    expect(construction[0]!.construction).toBe(true);
    expect(of(constraints, 'symmetric').length).toBeGreaterThanOrEqual(2);
    expect(of(constraints, 'symmetric').every((c) => c.refs[2] === 'axis-v')).toBe(true);
    expect(of(constraints, 'vertical').map((c) => c.refs[0])).toContain('axis-v');
  });

  it('proposes estimated dimensions with an unknown unit, never as measured', () => {
    const { constraints } = infer([line('a', [0, 0], [0.4, 0]), circle('c', [0, 0.5], 0.1), arc('k', [0.5, 0.5], 0.1, 0, 90)]);
    const dims = constraints.filter((c) => c.type === 'length' || c.type === 'radius');
    expect(dims.map((d) => [d.type, d.refs[0]])).toEqual([
      ['length', 'a'],
      ['radius', 'c'],
      ['radius', 'k'],
    ]);
    for (const d of dims) {
      expect(d.source).toBe('estimated');
      expect(d.unit).toBe('unknown');
      expect(d.status).toBe('proposed');
    }
    expect(dims[0]!.value).toBeCloseTo(0.4, 9);
  });

  it('gives the same ids for the same primitives', () => {
    const ps = [line('a', [0, 0], [0.4, 0.003]), line('b', [0.405, 0.004], [0.5, 0.3])];
    expect(infer(ps).constraints.map((c) => c.id)).toEqual(infer(ps).constraints.map((c) => c.id));
  });
});
