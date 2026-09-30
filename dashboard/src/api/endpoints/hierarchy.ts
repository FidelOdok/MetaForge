import apiClient from '../client';
import type { HierarchyNode } from '../../types/hierarchy';

interface HierarchyTreeResponse {
  nodes: HierarchyNode[];
}

export async function getHierarchyTree(projectId?: string): Promise<HierarchyNode[]> {
  try {
    const params = projectId ? { project_id: projectId } : {};
    const { data } = await apiClient.get<HierarchyTreeResponse>('/twin/hierarchy', { params });
    return data.nodes;
  } catch {
    return [];
  }
}

export interface RealizeHierarchyNodeResult {
  nodeId: string;
  realizedByWorkProductId: string | null;
  instanceOfBomItemId: string | null;
}

/** FORGE-266 (gap G-C2): "Replace placeholder with part" -- attach or
 * replace a hierarchy node's REALIZED_BY (a cad_model work product) and/or
 * INSTANCE_OF (a BOMItem) geometry. */
export async function realizeHierarchyNode(
  nodeId: string,
  payload: { workProductId?: string; bomItemId?: string },
): Promise<RealizeHierarchyNodeResult> {
  const { data } = await apiClient.post<RealizeHierarchyNodeResult>(
    `/twin/hierarchy/${nodeId}/realize`,
    payload,
  );
  return data;
}
