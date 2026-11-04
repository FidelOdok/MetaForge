/**
 * The sketch-to-parametric compiler, end to end (SKP-001..006, RC-03).
 *
 * `interpret` runs once per sketch revision: fit the strokes, propose
 * constraints and dimensions, and report the degrees of freedom. Everything
 * after that edits the interpretation in place and re-solves from the
 * current primitives: accepting or rejecting a rule, typing a dimension,
 * taking a correction. Recognition never runs again for a numeric edit.
 *
 * Sizes: the first dimension a person confirms sets the scale. The whole
 * sketch is scaled about its centre so that dimension has the typed value,
 * the other estimated dimensions scale with it, and the mapping back to the
 * strokes is kept in `frame` so the shapes still sit over the drawing. The
 * unit stays `unknown` until a person says what it is.
 */

import { CONFIDENT, fitSketch, type FitOptions, type FitStroke } from './fit';
import { inferConstraints } from './constraints/infer';
import {
  DIMENSION_TYPES,
  type ConstraintStatus,
  type CorrectionChoice,
  type Primitive,
  type SketchConstraint,
  type SketchInterpretation,
  type SketchUnit,
  type Vec,
} from './model';
import { solveSketch } from './solver';

/** Model units per sketch unit, about `origin` (in sketch units). */
export interface SketchFrame {
  scale: number;
  origin: Vec;
}

export interface CompiledSketch extends SketchInterpretation {
  frame: SketchFrame;
}

export interface InterpretOptions extends FitOptions {
  interpretationId: string;
  sketchRef: string | null;
  planeId?: string | null;
}

export function interpret(strokes: readonly FitStroke[], opts: InterpretOptions): CompiledSketch {
  const fit = fitSketch(strokes, opts);
  const inferred = inferConstraints(fit.primitives, { tolerance: fit.report.tolerance });
  const primitives = [...fit.primitives, ...inferred.construction];
  const base: CompiledSketch = {
    interpretation_id: opts.interpretationId,
    sketch_ref: opts.sketchRef,
    plane_id: opts.planeId ?? null,
    space: 'sketch_unit',
    unit: 'unknown',
    primitives,
    constraints: inferred.constraints,
    profiles: fit.profiles,
    fit: fit.report,
    solve: null,
    frame: { scale: 1, origin: [0, 0] },
  };
  return resolve(base);
}

/** Re-solve the accepted constraints from the current primitives. */
export function resolve(sketch: CompiledSketch): CompiledSketch {
  const result = solveSketch(sketch.primitives, sketch.constraints);
  return { ...sketch, primitives: result.primitives, solve: result.report };
}

export function setStatus(sketch: CompiledSketch, ids: readonly string[], status: ConstraintStatus): CompiledSketch {
  const set = new Set(ids);
  return resolve({
    ...sketch,
    constraints: sketch.constraints.map((c) => (set.has(c.id) ? { ...c, status } : c)),
  });
}

/**
 * Accept every proposed geometric rule at or above `min` confidence
 * (dimensions stay as they are). Rules the others already imply are left
 * proposed rather than accepted, so one click never makes a redundant set.
 */
export function acceptConfident(sketch: CompiledSketch, min = 0.75): CompiledSketch {
  const ids = sketch.constraints
    .filter((c) => c.status === 'proposed' && !DIMENSION_TYPES.includes(c.type) && c.confidence >= min)
    .map((c) => c.id);
  const first = setStatus(sketch, ids, 'accepted');
  const added = new Set(ids);
  const implied = (first.solve?.redundant ?? []).filter((id) => added.has(id));
  if (!first.solve?.converged || implied.length === 0) return first;
  return setStatus(first, implied, 'proposed');
}

function centreOf(primitives: readonly Primitive[]): Vec {
  let x = 0;
  let y = 0;
  let n = 0;
  const add = (p: Vec) => {
    x += p[0];
    y += p[1];
    n += 1;
  };
  for (const p of primitives) {
    if (p.type === 'line') {
      add(p.start);
      add(p.end);
    } else if (p.type === 'spline') p.points.forEach(add);
    else add(p.center);
  }
  return n ? [x / n, y / n] : [0, 0];
}

function scalePrimitive(p: Primitive, k: number, o: Vec): Primitive {
  const s = (q: Vec): Vec => [o[0] + (q[0] - o[0]) * k, o[1] + (q[1] - o[1]) * k];
  switch (p.type) {
    case 'line':
      return { ...p, start: s(p.start), end: s(p.end) };
    case 'circle':
      return { ...p, center: s(p.center), radius: p.radius * k };
    case 'arc':
      return { ...p, center: s(p.center), radius: p.radius * k };
    case 'ellipse':
      return { ...p, center: s(p.center), major_radius: p.major_radius * k, minor_radius: p.minor_radius * k };
    case 'spline':
      return { ...p, points: p.points.map(s) };
  }
}

/** The current value of a dimension's quantity. */
export function measured(sketch: Pick<SketchInterpretation, 'primitives'>, c: SketchConstraint): number | null {
  const p = sketch.primitives.find((q) => q.id === c.refs[0]);
  if (!p) return null;
  if (c.type === 'length' && p.type === 'line') return Math.hypot(p.end[0] - p.start[0], p.end[1] - p.start[1]);
  if (c.type === 'radius' && (p.type === 'arc' || p.type === 'circle')) return p.radius;
  return null;
}

/**
 * A person typed a value for a dimension: it becomes `human_confirmed` and
 * accepted, and the sketch re-solves from where it is (RC-03). The first
 * confirmed dimension sets the scale of the whole sketch.
 */
export function confirmDimension(sketch: CompiledSketch, id: string, value: number): CompiledSketch {
  const target = sketch.constraints.find((c) => c.id === id);
  if (!target || !DIMENSION_TYPES.includes(target.type) || !(value > 0) || !Number.isFinite(value)) return sketch;
  const firstScale = !sketch.constraints.some((c) => c.source === 'human_confirmed' && DIMENSION_TYPES.includes(c.type));
  let primitives = sketch.primitives;
  let constraints = sketch.constraints;
  let frame = sketch.frame;
  const current = measured(sketch, target);
  if (firstScale && current && current > 0) {
    const k = value / current;
    const o = centreOf(primitives);
    primitives = primitives.map((p) => scalePrimitive(p, k, o));
    constraints = constraints.map((c) =>
      DIMENSION_TYPES.includes(c.type) && c.source === 'estimated' && typeof c.value === 'number' ? { ...c, value: c.value * k } : c,
    );
    // Model = o + (sketch - o') * scale, composed with the earlier frame.
    const prevO = frame.origin;
    const prevS = frame.scale;
    // A point drawn at s maps to m = o + (prevO + (s - prevO) * prevS - o) * k.
    const scale = prevS * k;
    const origin: Vec = [
      (o[0] * (1 - k) + k * prevO[0] * (1 - prevS)) / (1 - scale) || 0,
      (o[1] * (1 - k) + k * prevO[1] * (1 - prevS)) / (1 - scale) || 0,
    ];
    frame = Math.abs(1 - scale) < 1e-12 ? frame : { scale, origin };
  }
  constraints = constraints.map((c) =>
    c.id === id ? { ...c, value, status: 'accepted' as const, source: 'human_confirmed' as const, unit: sketch.unit } : c,
  );
  return resolve({ ...sketch, primitives, constraints, frame });
}

/** Say what unit the confirmed dimensions are in. Values do not change. */
export function setUnit(sketch: CompiledSketch, unit: SketchUnit): CompiledSketch {
  return {
    ...sketch,
    unit,
    constraints: sketch.constraints.map((c) => (DIMENSION_TYPES.includes(c.type) ? { ...c, unit } : c)),
  };
}

/** Take one of a conflict's correction choices. */
export function applyChoice(sketch: CompiledSketch, choice: CorrectionChoice): CompiledSketch {
  if (choice.kind === 'drop') return setStatus(sketch, [choice.constraint_id], 'rejected');
  if (choice.value === undefined) return sketch;
  return confirmDimension(sketch, choice.constraint_id, choice.value);
}

/** A model-space point back to where it was drawn (for showing shapes over the strokes). */
export function toDrawn(frame: SketchFrame, p: Vec): Vec {
  if (frame.scale === 1) return p;
  return [frame.origin[0] + (p[0] - frame.origin[0]) / frame.scale, frame.origin[1] + (p[1] - frame.origin[1]) / frame.scale];
}

export function isConfident(p: { confidence: number }): boolean {
  return p.confidence >= CONFIDENT;
}

/** The wire shape the gateway stores (no client-only fields). */
export function toWire(sketch: CompiledSketch): SketchInterpretation & { frame: SketchFrame } {
  const { frame, ...rest } = sketch;
  return { ...rest, frame };
}
