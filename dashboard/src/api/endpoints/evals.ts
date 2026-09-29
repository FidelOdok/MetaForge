import apiClient from '../client';
import type { EvalsReport } from '../../types/evals';

const EMPTY: EvalsReport = { scenarios: [], history: [] };

export async function getEvals(): Promise<EvalsReport> {
  try {
    const { data } = await apiClient.get<EvalsReport>('/evals');
    return data;
  } catch {
    return EMPTY;
  }
}
