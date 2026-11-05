import type { PlaneElement, PreviewDefinition } from '../definition';

/** A preview definition as the gateway returns it, with what a test overrides. */
export function definition(over: Partial<PreviewDefinition> = {}): PreviewDefinition {
  return {
    schema: 'metaforge.feature_definition/1',
    op: 'extrude',
    elements: [{ type: 'rectangle', id: 'r', origin: [0, 0], width: 40, height: 20 }],
    frame: { origin: [0, 0, 0], x_dir: [1, 0, 0], y_dir: [0, 1, 0], normal: [0, 0, 1] },
    scale_to_mm: 1,
    depth: 5,
    definition_hash: 'hash-5',
    sketch_key: 'PSK-SLOT',
    sketch_ref: 'PSK-SLOT@1',
    base_revision: 1,
    feature_id: 'feat-extrude-1',
    features: [{ id: 'feat-extrude-1', op: 'extrude' }],
    parameters: {
      depth: { value: 5, status: 'provided', unit: 'mm', question: '', assumed: false },
      material: { value: 'PLA', status: 'provided', unit: null, question: '', assumed: false },
    },
    missing: [],
    unit: 'mm',
    scale_assumed: false,
    plane_ref: 'PLN-SLOT@1',
    plane_assumed: false,
    plane_blocked: null,
    approved: true,
    buildable: true,
    dimensions: [],
    handles: [{ kind: 'extrude_depth', parameter: 'depth', axis: 'normal' }],
    intended_through_holes: 0,
    comparison_tolerance: null,
    representation: { level: 'L2', authority: 'preview', label: 'Preview', source_revision: 'PSK-SLOT@1' },
    cad: {
      item_key: 'CAD-SLOT',
      exists: false,
      head_revision: null,
      node_id: null,
      geometry_state: null,
      definition_hash: null,
      matches_definition: false,
      comparison: null,
      latest_attempt: null,
      last_validated: null,
    },
    build: null,
    ...over,
  };
}

/** The rounded slot the gateway tests use, on the plane (v up): two lines and two arcs. */
export const SLOT: PlaneElement[] = [
  { type: 'line', id: 'l1', start: [0, 0], end: [40, 0] },
  { type: 'arc', id: 'a1', center: [40, -10], radius: 10, start_angle: 270, end_angle: 90 },
  { type: 'line', id: 'l2', start: [40, -20], end: [0, -20] },
  { type: 'arc', id: 'a2', center: [0, -10], radius: 10, start_angle: 90, end_angle: 270 },
];

/** A 60 x 30 plate with two round through-holes. */
export const PLATE_WITH_HOLES: PlaneElement[] = [
  { type: 'rectangle', id: 'r', origin: [0, 0], width: 60, height: 30 },
  { type: 'circle', id: 'c1', center: [15, 15], radius: 5 },
  { type: 'circle', id: 'c2', center: [45, 15], radius: 5 },
];

/** The CAD item once a validated revision was built from `hash`. */
export function validatedCad(hash: string, over: Partial<PreviewDefinition['cad']> = {}): PreviewDefinition['cad'] {
  return {
    item_key: 'CAD-SLOT',
    exists: true,
    head_revision: 2,
    node_id: 'cad-node-2',
    geometry_state: 'validated',
    definition_hash: hash,
    matches_definition: true,
    comparison: null,
    latest_attempt: null,
    last_validated: { revision: 2, node_id: 'cad-node-2', item_ref: 'CAD-SLOT@2', is_head: true },
    ...over,
  };
}
