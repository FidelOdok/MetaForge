import apiClient from '../client';
import type { ApprovalDecision, HarnessRun, RunStatus } from '../../types/run';

interface RunRaw {
  id: string;
  status: string;
  request: Record<string, unknown>;
  created_at: number;
  updated_at: number;
  error: string | null;
  approval_reason: string | null;
  result: Record<string, unknown> | null;
  history: string[];
}

interface RunListRaw {
  runs: RunRaw[];
}

function mapRun(raw: RunRaw): HarnessRun {
  return {
    id: raw.id,
    status: raw.status as RunStatus,
    request: raw.request ?? {},
    createdAt: raw.created_at,
    updatedAt: raw.updated_at,
    error: raw.error ?? undefined,
    approvalReason: raw.approval_reason ?? undefined,
    result: raw.result ?? undefined,
    history: (raw.history ?? []) as RunStatus[],
  };
}

/** List all runs via `GET /v1/runs`. */
export async function listRuns(): Promise<HarnessRun[]> {
  const { data } = await apiClient.get<RunListRaw>('/runs');
  return data.runs.map(mapRun);
}

/** Fetch one run via `GET /v1/runs/{id}`; undefined if not found. */
export async function getRun(id: string): Promise<HarnessRun | undefined> {
  try {
    const { data } = await apiClient.get<RunRaw>(`/runs/${id}`);
    return mapRun(data);
  } catch {
    return undefined;
  }
}

/** Create a run via `POST /v1/runs`. */
export async function createRun(
  request: Record<string, unknown> = {},
  start = true,
): Promise<HarnessRun> {
  const { data } = await apiClient.post<RunRaw>('/runs', { request, start });
  return mapRun(data);
}

/** Approve or reject a paused run via `POST /v1/runs/{id}/approval`. */
export async function submitRunApproval(
  id: string,
  decision: ApprovalDecision,
): Promise<HarnessRun> {
  const { data } = await apiClient.post<RunRaw>(`/runs/${id}/approval`, { decision });
  return mapRun(data);
}
