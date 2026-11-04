/**
 * The sketch compiler's data model: fitted primitives, proposed constraints
 * and the interpretation that holds them.
 *
 * Everything here lives in the sketch's own 2D space (u, v): the sketch
 * units the sketch layer stores strokes in, with +u to the right and +v
 * down the screen. Nothing here knows about a physical plane. A sketch's
 * `plane_id`, when one has been assigned, is carried through untouched.
 *
 * Field names are snake_case because this is also the wire shape the
 * gateway stores and re-checks (`twin_core/design_ir/models.py`).
 *
 * Angles on the wire are degrees, measured from +u towards +v. An arc runs
 * from `start_angle` to `end_angle` in that (increasing angle) direction.
 */

export type Vec = [number, number];

/** Where a primitive came from: one stroke and a range of its point indices. */
export interface StrokeRange {
  stroke_id: string;
  /** First and last index into the stroke's `points`, inclusive. */
  start: number;
  end: number;
}

interface PrimitiveBase {
  /** Stable: the same strokes give the same id (stroke id plus segment number). */
  id: string;
  source_stroke_ids: string[];
  source_ranges: StrokeRange[];
  /** Every fitted parameter is an estimate from a drawing, never a measurement. */
  estimated: boolean;
  /** 0..1, from the fit residual against the tolerance. */
  confidence: number;
  /** RMS distance of the source points from the fitted shape, in sketch units. */
  residual: number;
  /** Helper geometry (a symmetry axis): solved, never extruded. */
  construction?: boolean;
}

export interface LinePrim extends PrimitiveBase {
  type: 'line';
  start: Vec;
  end: Vec;
}

export interface CirclePrim extends PrimitiveBase {
  type: 'circle';
  center: Vec;
  radius: number;
}

export interface ArcPrim extends PrimitiveBase {
  type: 'arc';
  center: Vec;
  radius: number;
  start_angle: number;
  end_angle: number;
}

export interface EllipsePrim extends PrimitiveBase {
  type: 'ellipse';
  center: Vec;
  major_radius: number;
  minor_radius: number;
  /** Direction of the major axis, degrees. */
  rotation: number;
}

export interface SplinePrim extends PrimitiveBase {
  type: 'spline';
  /** Points the curve passes through (Catmull-Rom), in order. */
  points: Vec[];
  closed: boolean;
}

export type Primitive = LinePrim | CirclePrim | ArcPrim | EllipsePrim | SplinePrim;
export type PrimitiveType = Primitive['type'];

export type ConstraintType =
  | 'coincident'
  | 'horizontal'
  | 'vertical'
  | 'parallel'
  | 'perpendicular'
  | 'tangent'
  | 'equal'
  | 'concentric'
  | 'symmetric'
  | 'radius'
  | 'length';

export const DIMENSION_TYPES: readonly ConstraintType[] = ['radius', 'length'];

export type ConstraintStatus = 'proposed' | 'accepted' | 'rejected';
/**
 * `estimated`: a dimension read off the drawing. `inferred`: a geometric rule
 * guessed from the drawing. `human_confirmed`: a person typed or accepted it
 * as a value. Only `human_confirmed` is ever a number a person stands behind.
 */
export type ConstraintSource = 'estimated' | 'inferred' | 'human_confirmed';
export type SketchUnit = 'unknown' | 'mm' | 'in';

export interface SketchConstraint {
  id: string;
  type: ConstraintType;
  /** `<primitive id>` or `<primitive id>.<start|end|center>`. */
  refs: string[];
  value?: number | null;
  /** For a dimension: the sketch's unit, `unknown` until one is confirmed. */
  unit?: SketchUnit | 'deg' | null;
  status: ConstraintStatus;
  source: ConstraintSource;
  confidence: number;
  /** Tangency between two circles or arcs: touching outside or inside. */
  variant?: 'external' | 'internal' | null;
}

export interface ClosedProfile {
  primitive_ids: string[];
}

export interface FitReport {
  /** Sketch extent (bounding box diagonal) and the tolerance used, sketch units. */
  extent: number;
  tolerance: number;
  /** Screen pixels per sketch unit when the fit ran (screen-scale awareness). */
  unit_px: number;
  stroke_count: number;
  /** Strokes too short to fit (a dot or a tap). */
  skipped_stroke_ids: string[];
  duration_ms: number;
}

export type SolveStatus = 'fully' | 'under' | 'over' | 'not_solved';

export interface Conflict {
  /** The constraints that cannot all hold together. */
  constraint_ids: string[];
  message: string;
  choices: CorrectionChoice[];
}

export interface CorrectionChoice {
  kind: 'drop' | 'relax';
  constraint_id: string;
  /** For `relax`: the value the dimension would take for the others to hold. */
  value?: number;
  label: string;
}

export interface SolveReport {
  status: SolveStatus;
  /** Free parameters left, including moving and turning the whole sketch. */
  dof: number;
  /** Free parameters that change the shape or size (dof minus rigid motion). */
  shape_dof: number;
  converged: boolean;
  iterations: number;
  /** Largest residual of an accepted constraint after the solve. */
  max_residual: number;
  residuals: Record<string, number>;
  conflicts: Conflict[];
  /** Accepted constraints already implied by others (harmless, but worth knowing). */
  redundant: string[];
  message: string;
  duration_ms: number;
}

/** A proposal: primitives plus constraints, made from one sketch revision. */
export interface SketchInterpretation {
  /** The client's id for this proposal; the server stores one revision per id. */
  interpretation_id: string;
  /** The `sketch_strokes` revision it was made from (`SKT-...@n`). */
  sketch_ref: string | null;
  /** Carried through untouched; the compiler never reads it. */
  plane_id: string | null;
  space: 'sketch_unit';
  unit: SketchUnit;
  primitives: Primitive[];
  constraints: SketchConstraint[];
  profiles: ClosedProfile[];
  fit: FitReport;
  solve: SolveReport | null;
}

/** A point on a primitive named by a ref. */
export type PointKey = 'start' | 'end' | 'center';

export function parseRef(ref: string): { id: string; key: PointKey | null } {
  const dot = ref.lastIndexOf('.');
  if (dot > 0) {
    const key = ref.slice(dot + 1);
    if (key === 'start' || key === 'end' || key === 'center') return { id: ref.slice(0, dot), key };
  }
  return { id: ref, key: null };
}

export const DEG = 180 / Math.PI;
export const RAD = Math.PI / 180;

/** A primitive's named point, or null when it has none. */
export function pointOf(p: Primitive, key: PointKey): Vec | null {
  switch (p.type) {
    case 'line':
      return key === 'start' ? p.start : key === 'end' ? p.end : null;
    case 'arc': {
      if (key === 'center') return p.center;
      const a = (key === 'start' ? p.start_angle : p.end_angle) * RAD;
      return [p.center[0] + p.radius * Math.cos(a), p.center[1] + p.radius * Math.sin(a)];
    }
    case 'circle':
    case 'ellipse':
      return key === 'center' ? p.center : null;
    case 'spline': {
      if (p.closed || key === 'center') return null;
      return key === 'start' ? (p.points[0] ?? null) : (p.points[p.points.length - 1] ?? null);
    }
  }
}

/** The open ends of a primitive (none for a closed one). */
export function endpointKeys(p: Primitive): PointKey[] {
  if (p.type === 'line' || p.type === 'arc') return ['start', 'end'];
  if (p.type === 'spline' && !p.closed) return ['start', 'end'];
  return [];
}

export function isClosedPrimitive(p: Primitive): boolean {
  return p.type === 'circle' || p.type === 'ellipse' || (p.type === 'spline' && p.closed);
}

export function lineLength(p: LinePrim): number {
  return Math.hypot(p.end[0] - p.start[0], p.end[1] - p.start[1]);
}

/** Counter-clockwise sweep of an arc in degrees, in (0, 360). */
export function arcSweep(p: ArcPrim): number {
  let s = (p.end_angle - p.start_angle) % 360;
  if (s <= 0) s += 360;
  return s;
}
