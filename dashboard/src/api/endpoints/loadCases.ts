import apiClient from '../client';
import type {
  CreateLoadCasePayload,
  LoadCase,
  NamedFacesResponse,
} from '../../types/loadCase';

interface LoadCaseListResponse {
  loadCases: LoadCase[];
  total: number;
}

export async function getLoadCases(projectId?: string): Promise<LoadCase[]> {
  try {
    const params = projectId ? { project_id: projectId } : {};
    const { data } = await apiClient.get<LoadCaseListResponse>('/simulation/load-cases', {
      params,
    });
    return data.loadCases;
  } catch {
    return [];
  }
}

export async function createLoadCase(payload: CreateLoadCasePayload): Promise<LoadCase> {
  const { data } = await apiClient.post<LoadCase>('/simulation/load-cases', payload);
  return data;
}

/** FORGE-277: named-face geometry for an already-generated mesh, backing
 * the load-case dialog's 3D face picker. */
export async function getNamedFaces(meshFile: string): Promise<NamedFacesResponse> {
  const { data } = await apiClient.post<NamedFacesResponse>('/simulation/named-faces', {
    meshFile,
  });
  return data;
}
