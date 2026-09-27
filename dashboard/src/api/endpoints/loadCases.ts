import apiClient from '../client';
import type { CreateLoadCasePayload, LoadCase } from '../../types/loadCase';

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
