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
