import apiClient from '../client';
import type { SimulationResult } from '../../types/simulationResult';

interface SimulationResultListResponse {
  results: SimulationResult[];
  total: number;
}

export async function getSimulationResults(projectId?: string): Promise<SimulationResult[]> {
  try {
    const params = projectId ? { project_id: projectId } : {};
    const { data } = await apiClient.get<SimulationResultListResponse>('/simulation/results', {
      params,
    });
    return data.results;
  } catch {
    return [];
  }
}
