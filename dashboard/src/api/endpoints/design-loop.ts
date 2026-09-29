import apiClient from '../client';
import type {
  DesignLoopIteration,
  DesignLoopReport,
  StartDesignLoopPayload,
  StartDesignLoopResult,
} from '../../types/design-loop';

const EMPTY_REPORT: DesignLoopReport = { loop_id: '', iterations: [] };

/** FORGE-287: closed loop -- propose -> build -> simulate -> evaluate ->
 * revise -> repeat, persisting every candidate as a real DesignLoopIteration.
 * Errors propagate (unlike the read endpoints below) since a failed start
 * (e.g. an unknown work product) needs to reach the form's error state. */
export async function startDesignLoop(
  payload: StartDesignLoopPayload,
): Promise<StartDesignLoopResult> {
  const { data } = await apiClient.post<StartDesignLoopResult>('/design-loop/start', payload);
  return data;
}

export async function getDesignLoop(loopId?: string): Promise<DesignLoopReport> {
  if (!loopId) return EMPTY_REPORT;
  try {
    const { data } = await apiClient.get<DesignLoopReport>(`/design-loop/${loopId}`);
    return data;
  } catch {
    return EMPTY_REPORT;
  }
}

export async function approveDesignLoop(
  loopId: string,
  approvedBy: string,
): Promise<DesignLoopIteration> {
  const { data } = await apiClient.post<DesignLoopIteration>(`/design-loop/${loopId}/approve`, {
    approvedBy,
  });
  return data;
}
