import axios from 'axios';
import apiClient from '../client';
import type { SimulationResult } from '../../types/simulationResult';
import type { SimFieldPayload } from '../../types/simulationField';
import { asFieldPayload } from '../../components/simulation/fieldMath';

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

/** Outcome of fetching one result's 3D field (FORGE-532). `not_stored` is
 * the expected answer for any result recorded before fields were persisted,
 * so it is a state, not an error. */
export type SimFieldFetch =
  | { status: 'ok'; payload: SimFieldPayload }
  | { status: 'not_stored' }
  | { status: 'invalid' };

/** `GET /v1/simulation/results/{id}/field`. The gateway serves it with
 * `Content-Encoding: gzip`, so the browser inflates the JSON before axios
 * sees it. */
export async function getSimulationField(resultId: string): Promise<SimFieldFetch> {
  try {
    const { data } = await apiClient.get<unknown>(
      `/simulation/results/${encodeURIComponent(resultId)}/field`,
      { timeout: 60_000 },
    );
    const payload = asFieldPayload(data);
    return payload ? { status: 'ok', payload } : { status: 'invalid' };
  } catch (error) {
    if (axios.isAxiosError(error) && error.response?.status === 404) {
      return { status: 'not_stored' };
    }
    throw error;
  }
}
