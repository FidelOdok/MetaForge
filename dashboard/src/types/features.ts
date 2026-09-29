/** FORGE-269 (gap G-D1): parametric feature library -- named, reusable
 * Design IR macros. Only two of the ticket's seven named features ship in
 * this first cut (bolt_pattern, rib); the rest are the same pattern,
 * deliberately deferred (see domain_agents/shared/design_ir_macros.py). */

export type FeatureType = 'bolt_pattern' | 'rib';

export interface BoltPatternParams {
  feature_type: 'bolt_pattern';
  plate_length_mm: number;
  plate_width_mm: number;
  plate_thickness_mm: number;
  hole_diameter_mm: number;
  hole_count: number;
  pattern_radius_mm: number;
}

export interface RibParams {
  feature_type: 'rib';
  length_mm: number;
  height_mm: number;
  thickness_mm: number;
}

export type FeatureParams = BoltPatternParams | RibParams;

export interface GenerateFeaturePayload {
  name: string;
  workProductId?: string;
  feature: FeatureParams;
  adapter?: 'freecad' | 'cadquery';
  material?: string;
  projectId?: string;
  commit?: boolean;
}

/** Mirrors generate_parametric_feature's own output shape (snake_case --
 * same pass-through precedent as the design-loop start/get endpoints,
 * since this route is a thin wrapper over the skill's own Pydantic model). */
export interface GenerateFeatureResult {
  feature_type: FeatureType;
  work_product_id: string | null;
  cad_file: string;
  entity_count: number;
  volume_mm3: number;
  surface_area_mm2: number;
  bounding_box: {
    min_x: number;
    min_y: number;
    min_z: number;
    max_x: number;
    max_y: number;
    max_z: number;
  };
  material: string;
  committed: boolean;
  twin_node_id: string | null;
  model_url: string | null;
  commit_error: string | null;
  already_committed: boolean;
}
