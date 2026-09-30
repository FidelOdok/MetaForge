import apiClient from '../client';
import type { FlowRunState } from '../../types/run-flow';

/** Phase-by-phase state of a design-flow run (FORGE-396). */
export async function getFlowState(runId: string): Promise<FlowRunState> {
  const { data } = await apiClient.get<FlowRunState>(`/runs/${runId}/flow-state`);
  return data;
}
