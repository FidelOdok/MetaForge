import apiClient from '../client';
import type { BomComponent, HierarchicalBomLine } from '../../types/bom';

interface BomListResponse {
  components: BomComponent[];
  total: number;
}

export async function getBom(projectId?: string): Promise<BomComponent[]> {
  try {
    const params = projectId ? { project_id: projectId } : {};
    const { data } = await apiClient.get<BomListResponse>('/bom', { params });
    return data.components;
  } catch {
    return [];
  }
}

interface HierarchicalBomListResponse {
  lines: HierarchicalBomLine[];
  total: number;
}

export async function getHierarchicalBom(projectId?: string): Promise<HierarchicalBomLine[]> {
  try {
    const params = projectId ? { project_id: projectId } : {};
    const { data } = await apiClient.get<HierarchicalBomListResponse>('/bom/hierarchical', {
      params,
    });
    return data.lines;
  } catch {
    return [];
  }
}
