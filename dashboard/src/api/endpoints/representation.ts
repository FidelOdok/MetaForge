import apiClient from '../client';

/**
 * Representation levels and component maturity (C3D slice 2).
 *
 * `makeStructuredMesh` draws the concept's block layout as simple 3D parts
 * (L1): one node per component, sizes estimated, never exact. It is free and
 * fast, so it answers at once; an unchanged layout returns the mesh already
 * made. `getMaturity` answers, per component, how far its geometry has got,
 * with the evidence for each claim.
 */

const enc = encodeURIComponent;

export interface StructuredMeshResult {
  node_id: string;
  name: string;
  level: 'L1';
  authority: 'structured';
  dimensions: 'estimated';
  label: string;
  source_revision: string | null;
  component_ids: Record<string, string>;
  nodes: { component_id: string | null; name: string; node: string }[];
  triangle_count: number;
  reused: boolean;
}

export async function makeStructuredMesh(projectId: string, layoutNodeId?: string): Promise<StructuredMeshResult> {
  const response = await apiClient.post<StructuredMeshResult>(`/twin/projects/${enc(projectId)}/structured-mesh`, {
    layout_node_id: layoutNodeId ?? null,
  });
  return response.data;
}

export type ComponentMaturityName = 'Illustrative' | 'Structured' | 'Parametric' | 'Exact' | 'Verified' | 'Released';

export interface MaturityEvidence {
  maturity: ComponentMaturityName;
  kind: string;
  node_id: string | null;
  ref: string | null;
  detail: string;
}

export interface ComponentMaturity {
  component_id: string;
  name: string;
  required: boolean;
  source: string;
  maturity: ComponentMaturityName;
  evidence: MaturityEvidence[];
  next_step: string | null;
}

export interface ProjectMaturity {
  project_id: string;
  components: ComponentMaturity[];
  product: {
    maturity: ComponentMaturityName | null;
    by_maturity: Record<string, string[]>;
    required_count: number;
    component_count: number;
    limited_by: string[];
    claims: { exact: boolean; verified: boolean; released: boolean };
    summary: string;
  };
  has_assembly: boolean;
}

export async function getMaturity(projectId: string, signal?: AbortSignal): Promise<ProjectMaturity> {
  const response = await apiClient.get<ProjectMaturity>(`/twin/projects/${enc(projectId)}/maturity`, { signal });
  return response.data;
}
