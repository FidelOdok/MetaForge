/** FORGE-532: the 3D result field of one simulation_result, as served by
 * `GET /v1/simulation/results/{id}/field` (schema `metaforge.sim_field` v1,
 * built by the calculix adapter's `field_payload.py`). The outer surface of
 * the solved mesh, triangulated, with per-vertex scalar fields and an
 * optional displacement vector. */

export type FieldQuantity = 'von_mises' | 'displacement_magnitude' | 'temperature';

export interface FieldPeak {
  position: [number, number, number];
  value: number;
}

export interface ScalarField {
  label: string;
  unit: string;
  /** One value per vertex, in `positions` order. */
  values: number[];
  /** Range and peak over the FULL nodal field, before any decimation. */
  min: number;
  max: number;
  peak: FieldPeak | null;
}

export type FieldMarkerKind = 'fixture' | 'load' | 'heat_source' | 'sink';

export interface FieldMarker {
  kind: FieldMarkerKind;
  /** The node set it was located by, e.g. "Surface1". */
  label: string;
  position: [number, number, number];
  bbox?: { min: [number, number, number]; max: [number, number, number] };
  node_count?: number;
  /** Load force vector in newtons. */
  vector?: [number, number, number];
  /** Thermal source power (W) or sink temperature (C). */
  value?: number;
  unit?: string;
}

export interface SimFieldPayload {
  format: 'metaforge.sim_field';
  version: number;
  analysis_type: 'static_stress' | 'modal' | 'thermal' | string;
  units: Record<string, string>;
  vertex_count: number;
  triangle_count: number;
  /** Flat xyz per vertex. */
  positions: number[];
  /** Flat vertex indices, three per triangle, wound outward. */
  indices: number[];
  /** Flat dx,dy,dz per vertex, or null (thermal). */
  displacement: number[] | null;
  fields: Partial<Record<FieldQuantity, ScalarField>>;
  markers: FieldMarker[];
  bbox: { min: [number, number, number]; max: [number, number, number] };
  decimation: { applied: boolean; source_triangle_count: number; cell_size_mm: number | null };
}

/** Mesh convergence verdict as `calculix.check_mesh_convergence` returns it,
 * recorded on the finest run's simulation_result. */
export interface MeshConvergencePoint {
  element_size_mm: number;
  max_von_mises_mpa: number;
  element_count?: number;
}

export interface MeshConvergence {
  points: MeshConvergencePoint[];
  changes?: Array<{ from_element_size_mm: number; to_element_size_mm: number; percent_change: number }>;
  converged: boolean;
  recommendation?: string;
}
