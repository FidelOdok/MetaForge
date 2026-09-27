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
