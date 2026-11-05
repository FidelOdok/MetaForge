/**
 * A line-length drag handle on the L2 preview (Strategy A, spec section 8).
 *
 * The handle edits one accepted length dimension. Every move re-solves the
 * sketch's accepted rules in the browser with the client solver
 * (`lib/sketch/solver`), so the lines joined to the dragged one follow it
 * and every accepted rule keeps holding. The preview is redrawn from the
 * solved geometry; nothing is stored until release, when the new length and
 * the solved positions are committed once through the parameters route,
 * pinned to the revision the browser edited. A value the accepted rules
 * cannot all hold is refused, with the solver's plain-language reason.
 *
 * Pure (no React, no network), so it is tested without a canvas.
 */

import { solveSketch } from '../sketch/solver';
import type { Primitive, SketchConstraint, Vec } from '../sketch/model';
import type { PlaneElement, PreviewDefinition } from './definition';

/** A sketch element as the gateway stores it (sketch space, y down). */
export interface SolverElement {
  id?: string | null;
  type: string;
  construction?: boolean;
  [field: string]: unknown;
}

/** The sketch the browser re-solves a length drag over. */
export interface SolverSketch {
  elements: SolverElement[];
  /** Accepted rules only. */
  constraints: SketchConstraint[];
}

/** Solved positions, sketch space, by element id. */
export type SolvedGeometry = Record<string, Primitive>;

export type LengthSolve =
  | { ok: true; constraintId: string; value: number; solved: SolvedGeometry }
  | { ok: false; constraintId: string; value: number; message: string };

const SOLVABLE = new Set(['line', 'circle', 'arc', 'ellipse', 'spline']);
const GEOMETRY_FIELDS: Record<string, readonly string[]> = {
  line: ['start', 'end'],
  circle: ['center', 'radius'],
  arc: ['center', 'radius', 'start_angle', 'end_angle'],
  ellipse: ['center', 'major_radius', 'minor_radius', 'rotation'],
  spline: ['points'],
};

function fmt(v: number): string {
  return Number.isInteger(v) ? String(v) : v.toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
}

/** The gateway's elements as solver primitives (the fit provenance is not needed to solve). */
export function solverPrimitives(sketch: SolverSketch): Primitive[] {
  return sketch.elements
    .filter((e) => !!e.id && SOLVABLE.has(e.type))
    .map(
      (e) =>
        ({
          source_stroke_ids: [],
          source_ranges: [],
          estimated: false,
          confidence: 1,
          residual: 0,
          ...e,
          id: String(e.id),
        }) as unknown as Primitive,
    );
}

function sameGeometry(a: Primitive, b: Primitive): boolean {
  const fields = GEOMETRY_FIELDS[a.type] ?? [];
  return fields.every((f) => JSON.stringify((a as never)[f]) === JSON.stringify((b as never)[f]));
}

/**
 * Re-solve the sketch with one length dimension set to `value`, starting from
 * where the last solve of this drag left the geometry (`from`).
 */
export function solveLength(
  def: PreviewDefinition,
  constraintId: string,
  value: number,
  from: SolvedGeometry = {},
  pending: Record<string, number> = {},
): LengthSolve {
  const sketch = def.solver_sketch;
  const dim = def.dimensions.find((d) => d.constraint_id === constraintId && d.type === 'length');
  const unit = dim?.unit && dim.unit !== 'unknown' ? ` ${dim.unit}` : '';
  if (!sketch || !dim) {
    return { ok: false, constraintId, value, message: 'This length cannot be changed here: the sketch to solve it with is not available.' };
  }
  if (!(value > 0) || !Number.isFinite(value)) {
    return { ok: false, constraintId, value, message: 'A length must be above zero.' };
  }
  const base = solverPrimitives(sketch);
  const start = base.map((p) => from[p.id] ?? p);
  // Every length edited and not yet committed holds too, not only this one.
  const values: Record<string, number> = { ...pending, [constraintId]: value };
  const constraints = sketch.constraints.map((c) =>
    values[c.id] !== undefined ? { ...c, value: values[c.id]!, source: 'human_confirmed' as const, status: 'accepted' as const } : c,
  );
  const result = solveSketch(start, constraints, { maxDropTests: 8 });
  if (result.invalid.length) {
    return {
      ok: false,
      constraintId,
      value,
      message: `A length of ${fmt(value)}${unit} cannot be checked: ${result.invalid[0]!.reason}. Nothing was changed.`,
    };
  }
  if (!result.report.converged || result.report.status === 'over') {
    return {
      ok: false,
      constraintId,
      value,
      message: `A length of ${fmt(value)}${unit} cannot hold with the other accepted rules. ${result.report.message} Nothing was changed.`,
    };
  }
  const byId = new Map(base.map((p) => [p.id, p]));
  const solved: SolvedGeometry = {};
  for (const p of result.primitives) {
    const was = byId.get(p.id);
    if (was && !sameGeometry(was, p)) solved[p.id] = p;
  }
  return { ok: true, constraintId, value, solved };
}

/** The browser's uncommitted length edits: values, the solved geometry, and why the last one failed. */
export interface LengthEdits {
  length: Record<string, number>;
  solved: SolvedGeometry;
  conflict: string | null;
}

export const NO_LENGTH_EDITS: LengthEdits = Object.freeze({ length: {}, solved: {}, conflict: null }) as LengthEdits;

/**
 * One more step of a length edit. A good solve replaces the geometry; a
 * conflict keeps the last good geometry and says why.
 */
export function applyLength(def: PreviewDefinition, edits: LengthEdits, constraintId: string, value: number): LengthEdits {
  const r = solveLength(def, constraintId, value, edits.solved, edits.length);
  if (!r.ok) return { ...edits, conflict: r.message };
  return { length: { ...edits.length, [constraintId]: value }, solved: r.solved, conflict: null };
}

const mod360 = (a: number) => ((a % 360) + 360) % 360;
const flip = (p: Vec): [number, number] => [p[0], -p[1]];

/** A solved primitive (sketch space, y down) on the plane (v up), as the exact path lowers it. */
export function toPlaneElement(p: Primitive, like: PlaneElement): PlaneElement {
  switch (p.type) {
    case 'line':
      return { ...(like as object), type: 'line', id: p.id, start: flip(p.start), end: flip(p.end) } as PlaneElement;
    case 'circle':
      return { ...(like as object), type: 'circle', id: p.id, center: flip(p.center), radius: p.radius } as PlaneElement;
    case 'arc':
      // Mirroring reverses the direction: the old end is the new start.
      return {
        ...(like as object),
        type: 'arc',
        id: p.id,
        center: flip(p.center),
        radius: p.radius,
        start_angle: mod360(-p.end_angle),
        end_angle: mod360(-p.start_angle),
      } as PlaneElement;
    case 'ellipse':
      return {
        ...(like as object),
        type: 'ellipse',
        id: p.id,
        center: flip(p.center),
        major_radius: p.major_radius,
        minor_radius: p.minor_radius,
        rotation: -p.rotation,
      } as PlaneElement;
    case 'spline':
      return { ...(like as object), type: 'spline', id: p.id, points: p.points.map(flip), closed: p.closed } as PlaneElement;
  }
}

/** The definition's plane elements with the solved ones in their place. */
export function withSolvedElements(elements: PlaneElement[], solved: SolvedGeometry): PlaneElement[] {
  if (!Object.keys(solved).length) return elements;
  return elements.map((el) => (el.id && solved[el.id] ? toPlaneElement(solved[el.id]!, el) : el));
}

/** What the parameters route takes: only the geometric fields of the moved elements. */
export function geometryPayload(solved: SolvedGeometry): Record<string, Record<string, unknown>> {
  const out: Record<string, Record<string, unknown>> = {};
  for (const [id, p] of Object.entries(solved)) {
    const fields = GEOMETRY_FIELDS[p.type] ?? [];
    out[id] = Object.fromEntries(fields.map((f) => [f, (p as never)[f]]));
  }
  return out;
}
