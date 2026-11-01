/** A reusable FEA boundary-condition definition (FORGE-278). */
export interface LoadCase {
  id: string;
  name: string;
  material: Record<string, unknown> | null;
  fixedNodeSet: string | null;
  loadNodeSet: string | null;
  loadForceN: [number, number, number] | null;
  sourceOfLoads: string | null;
  projectId: string;
  createdAt: string;
  updatedAt: string;
}

export interface CreateLoadCasePayload {
  name: string;
  projectId: string;
  material: Record<string, unknown>;
  fixedNodeSet: string;
  loadNodeSet: string;
  loadForceN: [number, number, number];
  sourceOfLoads?: string;
}

/** One geometric face of a generated mesh (FORGE-277) — real coordinates
 * for a face, not just its opaque gmsh-assigned name (e.g. "Surface1"). */
export interface NamedFace {
  name: string;
  centroidMm: [number, number, number];
  normal: [number, number, number];
  areaMm2: number;
  bboxMm: { min: [number, number, number]; max: [number, number, number] };
}

export interface NamedFacesResponse {
  meshFile: string;
  faces: NamedFace[];
}
