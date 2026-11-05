/**
 * The shared feature definition the L2 preview is drawn from (C3D slice 5).
 *
 * It is the design IR the exact path lowers for a parametric sketch, read
 * back by the gateway (`twin_core/design_ir/feature_definition.py`): the
 * profile's elements already on the sketch plane (`v` up), the plane's world
 * frame in millimetres (CadQuery's own named-plane conventions, so the
 * preview sits where the B-Rep will), and one extrude or revolve. There is
 * no second product definition in the browser.
 */

export type Vec2 = [number, number];
export type Vec3 = [number, number, number];

/** A design IR sketch element, on the plane (u right, v up). */
export type PlaneElement =
  | { type: 'line'; id?: string | null; start: Vec2; end: Vec2; construction?: boolean }
  | { type: 'arc'; id?: string | null; center: Vec2; radius: number; start_angle: number; end_angle: number; construction?: boolean }
  | { type: 'circle'; id?: string | null; center: Vec2; radius: number; construction?: boolean }
  | {
      type: 'ellipse';
      id?: string | null;
      center: Vec2;
      major_radius: number;
      minor_radius: number;
      rotation: number;
      construction?: boolean;
    }
  | { type: 'rectangle'; id?: string | null; origin: Vec2; width: number; height: number; construction?: boolean }
  | { type: 'spline'; id?: string | null; points: Vec2[]; closed: boolean; construction?: boolean };

export interface PlaneFrame {
  origin: Vec3;
  x_dir: Vec3;
  y_dir: Vec3;
  normal: Vec3;
}

export interface FeatureParameterView {
  value: number | string | null;
  status: 'required_input' | 'provided';
  unit: string | null;
  question: string;
  /** Shown at an assumed value because nobody has given one. Never sent to the exact path. */
  assumed: boolean;
}

export interface DimensionView {
  constraint_id: string;
  type: 'radius';
  primitive_id: string;
  value: number;
  unit: string;
  source: string;
  /** The circle's centre on the plane. */
  center: Vec2;
}

export interface ComparisonCheck {
  check:
    | 'bounding_box'
    | 'dimensions'
    | 'volume'
    | 'surface_distance'
    | 'features'
    | 'component_pose'
    | 'component_alignment'
    | 'interference';
  outcome: 'pass' | 'fail' | 'not_evaluated';
  detail: string;
  [value: string]: unknown;
}

export interface ComparisonRecord {
  outcome: 'consistent' | 'discrepancy' | 'incomplete';
  checks: ComparisonCheck[];
  discrepancies: ComparisonCheck[];
  tolerance: { abs_mm: number; rel: number; volume_rel: number; linear_mm: number; basis: string; source: string };
  /** `op` is `multi_feature` for a feature tree with no L2 preview (only `features` is measured). */
  preview: {
    op: string;
    definition_hash: string | null;
    volume_mm3: number | null;
    intended_through_holes: number | null;
    intended_topology?: { genus?: number; shells?: number | null; basis?: string; reason?: string };
    sketch_ref?: string;
  };
  item_ref?: string;
  compared_at: string;
}

export interface ExactAttempt {
  state?: string;
  outcome?: string;
  attempted_at?: string;
  job_id?: string | null;
}

export interface CadView {
  item_key: string;
  exists: boolean;
  head_revision: number | null;
  node_id: string | null;
  geometry_state: string | null;
  definition_hash: string | null;
  matches_definition: boolean;
  comparison: ComparisonRecord | null;
  latest_attempt: ExactAttempt | null;
  last_validated: { revision: number; node_id: string; item_ref: string; is_head: boolean } | null;
}

export interface BuildView {
  job_id: string;
  status: string;
  operation_id?: string;
  base_revision?: string | null;
  error?: string | null;
  result?: Record<string, unknown>;
}

/** `GET .../parametric-sketches/{key}/preview-definition`. */
export interface PreviewDefinition {
  schema: string;
  op: 'extrude' | 'revolve';
  elements: PlaneElement[];
  frame: PlaneFrame;
  /** Millimetres per sketch unit. */
  scale_to_mm: number;
  /** Extrude depth, in sketch units. */
  depth?: number;
  /** Revolve angle, degrees. */
  angle?: number;
  shell_thickness?: number;
  /** The exact path's hash of this definition; null while it is not buildable. */
  definition_hash: string | null;
  sketch_key: string;
  sketch_ref: string;
  base_revision: number;
  feature_id: string;
  features: { id: string; op: string }[];
  parameters: Record<string, FeatureParameterView>;
  missing: string[];
  unit: 'mm' | 'in' | 'unknown';
  scale_assumed: boolean;
  plane_ref: string | null;
  plane_assumed: boolean;
  plane_blocked: string | null;
  approved: boolean;
  buildable: boolean;
  dimensions: DimensionView[];
  handles: { kind: string; parameter?: string; constraint_id?: string; primitive_id?: string; axis?: string }[];
  intended_through_holes: number;
  comparison_tolerance: Record<string, unknown> | null;
  representation: { level: 'L2'; authority: 'preview'; label: string; source_revision: string | null };
  cad: CadView;
  build: BuildView | null;
}

/** What the person changed in the browser and has not committed yet. */
export interface PreviewOverrides {
  /** Depth (extrude) or angle (revolve), in the definition's units. */
  amount?: number;
  /** Radius of a circle, by its dimension's constraint id. */
  radius?: Record<string, number>;
}

/** The definition with the browser's uncommitted edits applied. Pure. */
export function withOverrides(def: PreviewDefinition, o: PreviewOverrides): PreviewDefinition {
  let out = def;
  if (o.amount !== undefined && Number.isFinite(o.amount) && o.amount > 0) {
    out = def.op === 'extrude' ? { ...out, depth: o.amount } : { ...out, angle: Math.min(o.amount, 360) };
  }
  const radii = o.radius ?? {};
  if (Object.keys(radii).length) {
    const byPrimitive = new Map<string, number>();
    for (const d of def.dimensions) {
      const r = radii[d.constraint_id];
      if (r !== undefined && Number.isFinite(r) && r > 0) byPrimitive.set(d.primitive_id, r);
    }
    out = {
      ...out,
      elements: out.elements.map((el) =>
        el.type === 'circle' && el.id && byPrimitive.has(el.id) ? { ...el, radius: byPrimitive.get(el.id)! } : el,
      ),
    };
  }
  return out;
}

/** The committed amount (depth or angle) of a definition. */
export function amountOf(def: PreviewDefinition): number {
  return def.op === 'extrude' ? Number(def.depth ?? 0) : Number(def.angle ?? 360);
}

/** Whether the browser holds edits the gateway has not seen. */
export function isDirty(def: PreviewDefinition, o: PreviewOverrides): boolean {
  if (o.amount !== undefined && Math.abs(o.amount - amountOf(def)) > 1e-9) return true;
  for (const d of def.dimensions) {
    const r = o.radius?.[d.constraint_id];
    if (r !== undefined && Math.abs(r - d.value) > 1e-9) return true;
  }
  return false;
}
